# Exercise PostgreSQL persistence only when test database credentials are supplied.
from __future__ import annotations
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from psycopg.errors import CheckViolation
from app import Main
from app.metrics import calculate_results
from app.models import BacktestConfig, PositionState, Trade
from database.connection import connect_database
from database.run_repository import complete_run, fail_run, get_run, start_run
from database.strategy_repository import get_strategy_version

Database_name = 'strategy_app_test'
Password_variable = 'STRATEGY_APP_TEST_DB_PASSWORD'
Summary_fields = ('total_trades', 'winning_trades', 'losing_trades',
    'win_rate', 'total_net_pnl', 'average_trade', 'profit_factor', 'max_drawdown')
Strategy_source = '''from app.models import Signal
class TestStrategy:
    def evaluate(self, current_bar, prior_bars):
        return Signal.HOLD
'''


def checked_connection():
    '''Verify the test database and public schema before any writes.'''
    conn = connect_database(database=Database_name, user='postgres',
        password=os.environ[Password_variable])
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT current_database(), current_schema()')
            actual_database, actual_schema = cur.fetchone()
        if actual_database != Database_name or actual_schema != 'public':
            raise RuntimeError(f'Refusing test writes to {actual_database!r}.{actual_schema!r}')
    except Exception:
        conn.close()
        raise
    return conn


@unittest.skipUnless(os.environ.get(Password_variable),
    f'Set {Password_variable} to run PostgreSQL integration tests')
class LiveDatabaseTests(unittest.TestCase):
    '''Use a unique strategy and remove only rows linked to that strategy.'''

    def setUp(self):
        self.conn = checked_connection()
        self.addCleanup(self.conn.close)
        self.strategy_id = None
        self.version_id = None
        self.run_ids = []
        self.strategy_name = f'codex_test_{uuid4().hex}'
        self.addCleanup(self.cleanup_owned_rows)
        with self.conn.transaction():
            with self.conn.cursor() as cur:
                cur.execute('INSERT INTO public.strategies (name) VALUES (%s) RETURNING strategy_id',
                    (self.strategy_name,))
                self.strategy_id = cur.fetchone()[0]
                cur.execute('''INSERT INTO public.strategy_versions
                    (strategy_id, version_number, entrypoint_name, source_code)
                    VALUES (%s, %s, %s, %s) RETURNING strategy_version_id''',
                    (self.strategy_id, 1, 'TestStrategy', Strategy_source))
                self.version_id = cur.fetchone()[0]

    def cleanup_owned_rows(self):
        '''Delete runs and fixtures under this test's unique strategy ID.'''
        if self.strategy_id is None:
            return
        conn = checked_connection()
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    if self.version_id is not None:
                        for run_id in self.run_ids:
                            cur.execute('''DELETE FROM public.backtest_runs
                                WHERE run_id = %s AND strategy_version_id = %s''',
                                (run_id, self.version_id))
                        cur.execute('''DELETE FROM public.strategy_versions
                            WHERE strategy_version_id = %s AND strategy_id = %s''',
                            (self.version_id, self.strategy_id))
                    cur.execute('DELETE FROM public.strategies WHERE strategy_id = %s AND name = %s',
                        (self.strategy_id, self.strategy_name))
        finally:
            conn.close()

    def start_owned_run(self, conn, **kwargs) -> int:
        '''Record the ID of a committed run for targeted cleanup.'''
        run_id = start_run(conn, **kwargs)
        self.run_ids.append(run_id)
        return run_id

    def make_trades(self) -> tuple[Trade, Trade]:
        '''Return a $20 winner followed by a $100 loser.'''
        market_tz = timezone(timedelta(hours=-4))
        first = Trade(direction=PositionState.LONG, quantity=1,
            entry_timestamp=datetime(2024, 6, 11, 10, 0, tzinfo=market_tz),
            entry_price=19000.0,
            exit_timestamp=datetime(2024, 6, 11, 10, 2, tzinfo=market_tz),
            exit_price=19003.0, gross_points=3.0, gross_pnl=60.0,
            cost_dollars=40.0, net_pnl=20.0)
        second = Trade(direction=PositionState.SHORT, quantity=1,
            entry_timestamp=datetime(2024, 6, 11, 10, 3, tzinfo=market_tz),
            entry_price=19000.0,
            exit_timestamp=datetime(2024, 6, 11, 10, 5, tzinfo=market_tz),
            exit_price=19003.0, gross_points=-3.0, gross_pnl=-60.0,
            cost_dollars=40.0, net_pnl=-100.0)
        return first, second

    def test_completed_run_is_visible_through_new_connections(self):
        config = BacktestConfig(quantity=1, cost_points_per_trade=2.0)
        run_id = self.start_owned_run(self.conn, strategy_version_id=self.version_id,
            config=config, dataset_ref='sample.csv')

        # start_run must commit before a new connection can see the running row.
        reader = checked_connection()
        try:
            running = get_run(reader, run_id=run_id)
        finally:
            reader.close()
        self.assertEqual(running['status'], 'running')
        self.assertIsNone(running['dataset_sha256'])
        self.assertIsNone(running['engine_sha256'])
        self.assertEqual(running['trades'], [])

        result = calculate_results(self.make_trades())
        complete_run(self.conn, run_id=run_id, result=result,
            dataset_sha256='a' * 64, engine_sha256='b' * 64)

        reader = checked_connection()
        try:
            saved = get_run(reader, run_id=run_id)
        finally:
            reader.close()
        self.assertEqual(saved['status'], 'completed')
        self.assertEqual(saved['strategy_version_id'], self.version_id)
        self.assertEqual(saved['strategy_name'], self.strategy_name)
        self.assertEqual(saved['source_code'], Strategy_source)
        self.assertEqual((saved['dataset_sha256'], saved['engine_sha256']), ('a' * 64, 'b' * 64))
        self.assertEqual((saved['quantity'], saved['cost_points_per_trade']), (1, 2.0))
        self.assertEqual((saved['total_trades'], saved['winning_trades'],
            saved['losing_trades'], saved['total_net_pnl'], saved['max_drawdown']),
            (2, 1, 1, -80.0, 100.0))
        self.assertEqual(saved['profit_factor'], 0.2)
        self.assertEqual([trade['trade_number'] for trade in saved['trades']], [1, 2])
        self.assertEqual([trade['direction'] for trade in saved['trades']], ['long', 'short'])
        self.assertEqual(saved['trades'][0]['entry_timestamp'], datetime(2024, 6, 11, 10, 0))
        self.assertEqual(saved['trades'][0]['entry_utc_offset'], timedelta(hours=-4))

    def test_missing_csv_marks_the_committed_run_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / 'missing.csv'
            with patch.object(Main, 'DATASET', missing), \
                    patch.object(Main, 'start_run', side_effect=self.start_owned_run), \
                    redirect_stdout(io.StringIO()):
                with self.assertRaises(FileNotFoundError):
                    Main.run(self.conn, version_id=self.version_id, quantity=1, cost_points=2.0)

        self.assertEqual(len(self.run_ids), 1)
        reader = checked_connection()
        try:
            saved = get_run(reader, run_id=self.run_ids[0])
        finally:
            reader.close()
        self.assertEqual(saved['status'], 'failed')
        self.assertIsNotNone(saved['finished_at'])
        self.assertIn('FileNotFoundError:', saved['error_message'])
        self.assertIsNone(saved['dataset_sha256'])
        self.assertTrue(all(saved[field] is None for field in Summary_fields))
        self.assertEqual(saved['trades'], [])

    def test_failed_save_rolls_back_every_trade_and_metric(self):
        run_id = self.start_owned_run(self.conn, strategy_version_id=self.version_id,
            config=BacktestConfig(quantity=1, cost_points_per_trade=2.0),
            dataset_ref='sample.csv')
        first, second = self.make_trades()
        invalid_trade = replace(second, entry_price=-1.0)
        result = calculate_results((first, invalid_trade))

        with self.assertRaises(CheckViolation) as rejected:
            complete_run(self.conn, run_id=run_id, result=result,
                dataset_sha256='a' * 64, engine_sha256='b' * 64)
        self.assertEqual(rejected.exception.diag.constraint_name, 'trades_values_finite')

        reader = checked_connection()
        try:
            rolled_back = get_run(reader, run_id=run_id)
        finally:
            reader.close()
        self.assertEqual(rolled_back['status'], 'running')
        self.assertIsNone(rolled_back['dataset_sha256'])
        self.assertIsNone(rolled_back['engine_sha256'])
        self.assertTrue(all(rolled_back[field] is None for field in Summary_fields))
        self.assertEqual(rolled_back['trades'], [])

        fail_run(self.conn, run_id=run_id, error_message='Save failed')
        reader = checked_connection()
        try:
            failed = get_run(reader, run_id=run_id)
        finally:
            reader.close()
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['error_message'], 'Save failed')
        self.assertTrue(all(failed[field] is None for field in Summary_fields))
        self.assertEqual(failed['trades'], [])

    def test_repositories_use_public_when_search_path_excludes_it(self):
        with self.conn.cursor() as cur:
            cur.execute('SET search_path TO pg_catalog')
            cur.execute('SELECT current_schema()')
            self.assertEqual(cur.fetchone()[0], 'pg_catalog')

        version = get_strategy_version(self.conn, version_id=self.version_id)
        self.assertEqual(version['strategy_version_id'], self.version_id)
        config = BacktestConfig(quantity=1, cost_points_per_trade=2.0)
        run_id = self.start_owned_run(self.conn, strategy_version_id=self.version_id,
            config=config, dataset_ref='sample.csv')
        complete_run(self.conn, run_id=run_id, result=calculate_results(self.make_trades()),
            dataset_sha256='a' * 64, engine_sha256='b' * 64)
        self.assertEqual(get_run(self.conn, run_id=run_id)['status'], 'completed')

        failed_id = self.start_owned_run(self.conn, strategy_version_id=self.version_id,
            config=config, dataset_ref='sample.csv')
        fail_run(self.conn, run_id=failed_id, error_message='test failure')
        self.assertEqual(get_run(self.conn, run_id=failed_id)['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
