# Check that terminal cost input is explicit and allows a deliberate zero.
import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from app import Main
from app.models import Bar


class MainTests(unittest.TestCase):
    '''Verify argument handling without connecting to PostgreSQL.'''

    def make_bars(self, contract_prices):
        '''Build completed bars for a small in-memory run.'''
        start = datetime(2024, 6, 11, 14, 0, tzinfo=timezone.utc)
        return [Bar(timestamp=start + timedelta(minutes=index),
                open=price, high=price, low=price, close=price,
                volume=100, contract=contract, trade_date=start.date())
            for index, (contract, price) in enumerate(contract_prices)]

    def cleanup_strategy_modules(self, before):
        '''Remove only strategy modules registered by this test.'''
        for name in tuple(sys.modules):
            if name.startswith('db_strategy_') and name not in before:
                del sys.modules[name]

    def test_run_requires_cost_points_before_database_access(self):
        errors = io.StringIO()
        with patch('sys.argv', ['strategy_app', 'run', '1000']), \
                patch.object(Main, 'getpass') as getpass, \
                patch.object(Main, 'connect_database') as connect, \
                redirect_stderr(errors):
            with self.assertRaises(SystemExit) as raised:
                Main.main()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn('--cost-points', errors.getvalue())
        getpass.assert_not_called()
        connect.assert_not_called()

    def test_explicit_zero_cost_reaches_run(self):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.cursor.return_value.__enter__.return_value.fetchone.return_value = ('strategy_app_test',)
        with patch('sys.argv', ['strategy_app', 'run', '1000', '--cost-points', '0']), \
                patch.object(Main, 'getpass', return_value='ignored'), \
                patch.object(Main, 'connect_database', return_value=conn), \
                patch.object(Main, 'run') as run:
            self.assertEqual(Main.main(), 0)
        run.assert_called_once_with(conn, version_id=1000, quantity=1, cost_points=0.0)

    def test_run_starts_before_loading_and_completes_with_both_hashes(self):
        conn = object()
        version = {'strategy_version_id': 1000}
        strategy = object()
        result = SimpleNamespace(total_trades=0, total_net_pnl=0.0)
        order = []

        def start(*args, **kwargs):
            order.append('start')
            return 1001

        def load(path):
            order.append('load')
            return [], 'a' * 64

        with patch.object(Main, 'get_strategy_version', return_value=version), \
                patch.object(Main, 'start_run', side_effect=start) as start_run, \
                patch.object(Main, 'load_strategy', return_value=strategy), \
                patch.object(Main, 'load_nq_data_with_hash', side_effect=load), \
                patch.object(Main, 'sha256_engine', return_value='b' * 64), \
                patch.object(Main, 'run_backtest', return_value=()), \
                patch.object(Main, 'calculate_results', return_value=result), \
                patch.object(Main, 'complete_run') as complete_run, \
                redirect_stdout(io.StringIO()):
            run_id = Main.run(conn, version_id=1000, quantity=1, cost_points=0)

        self.assertEqual(run_id, 1001)
        self.assertEqual(order, ['start', 'load'])
        start_run.assert_called_once()
        self.assertEqual(start_run.call_args.kwargs['dataset_ref'], Main.DATASET.name)
        self.assertNotIn('dataset_sha256', start_run.call_args.kwargs)
        self.assertNotIn('engine_sha256', start_run.call_args.kwargs)
        self.assertEqual(start_run.call_args.kwargs['config'].cost_points_per_trade, 0)
        complete_run.assert_called_once_with(conn, run_id=1001, result=result,
            dataset_sha256='a' * 64, engine_sha256='b' * 64)

    def test_missing_csv_after_start_is_recorded_as_failed(self):
        conn = object()
        with patch.object(Main, 'get_strategy_version', return_value={}), \
                patch.object(Main, 'start_run', return_value=1001) as start_run, \
                patch.object(Main, 'load_strategy', return_value=object()), \
                patch.object(Main, 'load_nq_data_with_hash',
                    side_effect=FileNotFoundError('missing CSV')), \
                patch.object(Main, 'fail_run') as fail_run, \
                patch.object(Main, 'complete_run') as complete_run, \
                redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(FileNotFoundError, 'missing CSV'):
                Main.run(conn, version_id=1000, quantity=1, cost_points=2.0)

        start_run.assert_called_once()
        fail_run.assert_called_once_with(conn, run_id=1001,
            error_message='FileNotFoundError: missing CSV')
        complete_run.assert_not_called()

    def test_ctrl_c_after_start_is_recorded_as_failed(self):
        conn = object()
        with patch.object(Main, 'get_strategy_version', return_value={}), \
                patch.object(Main, 'start_run', return_value=1001), \
                patch.object(Main, 'load_strategy', return_value=object()), \
                patch.object(Main, 'load_nq_data_with_hash',
                    side_effect=KeyboardInterrupt('cancelled')), \
                patch.object(Main, 'fail_run') as fail_run, \
                patch.object(Main, 'complete_run') as complete_run, \
                redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(KeyboardInterrupt, 'cancelled'):
                Main.run(conn, version_id=1000, quantity=1, cost_points=2.0)

        fail_run.assert_called_once_with(conn, run_id=1001,
            error_message='KeyboardInterrupt: cancelled')
        complete_run.assert_not_called()

    def test_system_exit_during_execution_is_recorded_as_failed(self):
        conn = object()
        with patch.object(Main, 'get_strategy_version', return_value={}), \
                patch.object(Main, 'start_run', return_value=1001), \
                patch.object(Main, 'load_strategy', return_value=object()), \
                patch.object(Main, 'load_nq_data_with_hash', return_value=([], 'a' * 64)), \
                patch.object(Main, 'sha256_engine', return_value='b' * 64), \
                patch.object(Main, 'run_backtest', side_effect=SystemExit(7)), \
                patch.object(Main, 'fail_run') as fail_run, \
                patch.object(Main, 'complete_run') as complete_run, \
                redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                Main.run(conn, version_id=1000, quantity=1, cost_points=2.0)

        self.assertEqual(raised.exception.code, 7)
        fail_run.assert_called_once_with(conn, run_id=1001,
            error_message='SystemExit: 7')
        complete_run.assert_not_called()

    def test_strategy_compilation_error_after_start_is_recorded(self):
        conn = object()
        with patch.object(Main, 'get_strategy_version', return_value={}), \
                patch.object(Main, 'start_run', return_value=1001), \
                patch.object(Main, 'load_strategy', side_effect=SyntaxError('bad source')), \
                patch.object(Main, 'load_nq_data_with_hash') as load, \
                patch.object(Main, 'fail_run') as fail_run, \
                redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SyntaxError, 'bad source'):
                Main.run(conn, version_id=1000, quantity=1, cost_points=2.0)

        load.assert_not_called()
        fail_run.assert_called_once_with(conn, run_id=1001,
            error_message='SyntaxError: bad source')

    def test_save_error_marks_the_started_run_failed(self):
        conn = object()
        result = SimpleNamespace(total_trades=0, total_net_pnl=0.0)
        with patch.object(Main, 'get_strategy_version', return_value={}), \
                patch.object(Main, 'start_run', return_value=1001), \
                patch.object(Main, 'load_strategy', return_value=object()), \
                patch.object(Main, 'load_nq_data_with_hash', return_value=([], 'a' * 64)), \
                patch.object(Main, 'sha256_engine', return_value='b' * 64), \
                patch.object(Main, 'run_backtest', return_value=()), \
                patch.object(Main, 'calculate_results', return_value=result), \
                patch.object(Main, 'complete_run', side_effect=RuntimeError('save failed')), \
                patch.object(Main, 'fail_run') as fail_run, \
                redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'save failed'):
                Main.run(conn, version_id=1000, quantity=1, cost_points=2.0)

        fail_run.assert_called_once_with(conn, run_id=1001,
            error_message='RuntimeError: save failed')

    def test_stored_strategy_restarts_with_each_contract_and_saves_only_completed_trades(self):
        source = '''from app.models import Signal
creation_count = 0
class StatefulStrategy:
    def __init__(self):
        global creation_count
        creation_count += 1
        if creation_count != 1:
            raise AssertionError('Strategy module state crossed contracts')
        self.in_position = False
    def evaluate(self, current_bar, prior_bars):
        if any(previous.contract != current_bar.contract for previous in prior_bars):
            raise AssertionError('Prior bars crossed contracts')
        self.in_position = not self.in_position
        return Signal.BUY if self.in_position else Signal.EXIT
'''
        version = {'strategy_version_id': 91001,
            'entrypoint_name': 'StatefulStrategy', 'source_code': source}
        bars = self.make_bars((('NQM4', 19000), ('NQM4', 19001), ('NQM4', 19002),
            ('NQU4', 19500), ('NQU4', 19501)))
        conn = object()
        self.addCleanup(self.cleanup_strategy_modules, set(sys.modules))
        with patch.object(Main, 'get_strategy_version', return_value=version), \
                patch.object(Main, 'start_run', return_value=1001) as start_run, \
                patch.object(Main, 'load_nq_data_with_hash', return_value=(bars, 'a' * 64)), \
                patch.object(Main, 'complete_run') as complete_run, \
                patch.object(Main, 'fail_run') as fail_run, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(Main.run(conn, version_id=91001, quantity=1,
                cost_points=0.25), 1001)

        start_run.assert_called_once()
        complete_run.assert_called_once()
        fail_run.assert_not_called()
        saved = complete_run.call_args.kwargs
        self.assertEqual((saved['run_id'], saved['dataset_sha256']), (1001, 'a' * 64))
        self.assertRegex(saved['engine_sha256'], r'^[0-9a-f]{64}$')
        result = saved['result']
        self.assertEqual(result.total_trades, 2)
        self.assertEqual(result.total_net_pnl, 30.0)
        self.assertEqual([(trade.entry_timestamp, trade.exit_timestamp)
            for trade in result.trades], [(bars[0].timestamp, bars[1].timestamp),
                (bars[3].timestamp, bars[4].timestamp)])
        self.assertEqual([(trade.entry_price, trade.exit_price, trade.net_pnl)
            for trade in result.trades], [(19000, 19001, 15.0), (19500, 19501, 15.0)])

    def test_aggregate_cost_overflow_marks_run_failed_without_saving(self):
        source = '''from app.models import Signal
class AlternatingStrategy:
    def __init__(self):
        self.calls = 0
    def evaluate(self, current_bar, prior_bars):
        self.calls += 1
        return Signal.BUY if self.calls % 2 else Signal.EXIT
'''
        version = {'strategy_version_id': 91002,
            'entrypoint_name': 'AlternatingStrategy', 'source_code': source}
        bars = self.make_bars((('NQM4', 19000),) * 4)
        conn = object()
        self.addCleanup(self.cleanup_strategy_modules, set(sys.modules))
        with patch.object(Main, 'get_strategy_version', return_value=version), \
                patch.object(Main, 'start_run', return_value=1002) as start_run, \
                patch.object(Main, 'load_nq_data_with_hash', return_value=(bars, 'a' * 64)), \
                patch.object(Main, 'complete_run') as complete_run, \
                patch.object(Main, 'fail_run') as fail_run, \
                redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'finite|overflow'):
                Main.run(conn, version_id=91002, quantity=1, cost_points=8e306)

        start_run.assert_called_once()
        complete_run.assert_not_called()
        fail_run.assert_called_once()
        self.assertEqual(fail_run.call_args.kwargs['run_id'], 1002)
        self.assertRegex(fail_run.call_args.kwargs['error_message'],
            r'^ValueError: .*?(finite|overflow)')

    def test_invalid_settings_and_unknown_version_do_not_start_a_run(self):
        conn = object()
        with patch.object(Main, 'get_strategy_version') as get_version, \
                patch.object(Main, 'start_run') as start_run:
            with self.assertRaises(ValueError):
                Main.run(conn, version_id=1000, quantity=0, cost_points=2.0)
            get_version.assert_not_called()
            start_run.assert_not_called()

            get_version.side_effect = ValueError('unknown version')
            with self.assertRaisesRegex(ValueError, 'unknown version'):
                Main.run(conn, version_id=9999, quantity=1, cost_points=2.0)
            start_run.assert_not_called()

    def test_overflowing_cost_is_rejected_before_starting_a_run(self):
        with patch.object(Main, 'get_strategy_version') as get_version, \
                patch.object(Main, 'start_run') as start_run:
            with self.assertRaisesRegex(ValueError, 'finite dollar cost'):
                Main.run(object(), version_id=1000, quantity=1, cost_points=1e308)
        get_version.assert_not_called()
        start_run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
