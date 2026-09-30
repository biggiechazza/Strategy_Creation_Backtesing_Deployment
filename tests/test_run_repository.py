# Test persistence contracts without a PostgreSQL connection.
from __future__ import annotations
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from app.models import BacktestConfig, BacktestResult, PositionState, Trade
from database.run_repository import complete_run, get_run, start_run


class FakeConnection:
    def __init__(self, *, rows=(), trades=(), fail_update=False):
        self.rows = iter(rows)
        self.trades = trades
        self.fail_update = fail_update
        self.events = []
        self.committed = []
        self.pending = []
        self.in_transaction = False
        self.info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)
        self.row_factory = None

    def transaction(self):
        return FakeTransaction(self)

    def cursor(self, *, row_factory=None):
        self.row_factory = row_factory
        return FakeCursor(self)


class FakeTransaction:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        if self.conn.in_transaction:
            raise AssertionError('unexpected nested transaction')
        self.conn.in_transaction = True
        self.conn.events.append('begin')
        return self

    def __exit__(self, error_type, error, traceback):
        if error_type is None:
            self.conn.committed.extend(self.conn.pending)
            self.conn.events.append('commit')
        else:
            self.conn.events.append('rollback')
        self.conn.pending.clear()
        self.conn.in_transaction = False
        return False


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, error_type, error, traceback):
        return False

    def execute(self, query, params=None):
        if not self.conn.in_transaction:
            raise AssertionError('query executed outside transaction')
        if self.conn.fail_update and 'UPDATE backtest_runs' in query:
            raise RuntimeError('database rejected update')
        command = ('execute', query, params)
        self.conn.events.append(command)
        self.conn.pending.append(command)

    def executemany(self, query, params):
        if not self.conn.in_transaction:
            raise AssertionError('batch executed outside transaction')
        command = ('executemany', query, tuple(params))
        self.conn.events.append(command)
        self.conn.pending.append(command)

    def fetchone(self):
        return next(self.conn.rows)

    def fetchall(self):
        return self.conn.trades


def one_trade_result():
    market_tz = timezone(timedelta(hours=-4))
    trade = Trade(direction=PositionState.LONG,
        quantity=1,
        entry_timestamp=datetime(2024, 6, 11, 10, 0, tzinfo=market_tz),
        entry_price=19000.0,
        exit_timestamp=datetime(2024, 6, 11, 10, 2, tzinfo=market_tz),
        exit_price=19003.0,
        gross_points=3.0,
        gross_pnl=60.0,
        cost_dollars=40.0,
        net_pnl=20.0,)
    return BacktestResult(trades=(trade,), total_trades=1, winning_trades=1, losing_trades=0,
        win_rate=1.0, total_net_pnl=20.0, average_trade=20.0,
        profit_factor=None, max_drawdown=0.0,)


class RunRepositoryTests(unittest.TestCase):
    def test_start_commits_running_row_without_hashes(self):
        conn = FakeConnection(rows=[(1000,)])

        run_id = start_run(conn, strategy_version_id=1001,
            config=BacktestConfig(quantity=2, cost_points_per_trade=0.25),
            dataset_ref='sample.csv')

        self.assertEqual(run_id, 1000)
        self.assertEqual(conn.events[0], 'begin')
        self.assertEqual(conn.events[-1], 'commit')
        self.assertEqual(len(conn.committed), 1)
        _, sql, params = conn.committed[0]
        self.assertIn('INSERT INTO backtest_runs', sql)
        self.assertNotIn('dataset_sha256', sql)
        self.assertNotIn('engine_sha256', sql)
        self.assertEqual(params, (1001, 'sample.csv', 2, 0.25))

    def test_complete_commits_trades_hashes_metrics_and_status_together(self):
        conn = FakeConnection(rows=[(1000,)])
        dataset_hash = 'a' * 64
        engine_hash = 'b' * 64

        complete_run(conn, run_id=1000, result=one_trade_result(),
            dataset_sha256=dataset_hash, engine_sha256=engine_hash)

        self.assertEqual(conn.events[0], 'begin')
        self.assertEqual(conn.events[-1], 'commit')
        self.assertEqual(len(conn.committed), 2)
        batch, update = conn.committed
        self.assertEqual(batch[0], 'executemany')
        self.assertIn('INSERT INTO trades', batch[1])
        self.assertEqual(len(batch[2]), 1)
        self.assertEqual(batch[2][0][:4], (1000, 1, 'long', 1))
        self.assertEqual(update[0], 'execute')
        self.assertIn("status = 'completed'", update[1])
        self.assertIn('dataset_sha256 = %s, engine_sha256 = %s', update[1])
        self.assertEqual(update[2][:2], (dataset_hash, engine_hash))
        self.assertEqual(update[2][-1], 1000)

    def test_failed_completion_rolls_back_the_trade_batch(self):
        conn = FakeConnection(fail_update=True)

        with self.assertRaisesRegex(RuntimeError, 'database rejected update'):
            complete_run(conn, run_id=1000, result=one_trade_result(),
                dataset_sha256='a' * 64, engine_sha256='b' * 64)

        self.assertEqual(conn.events[0], 'begin')
        self.assertEqual(conn.events[-1], 'rollback')
        self.assertTrue(any(event[0] == 'executemany' for event in conn.events[1:-1]))
        self.assertEqual(conn.committed, [])

    def test_get_run_reads_header_and_trades_in_one_repeatable_read_snapshot(self):
        saved = {'run_id': 1000, 'status': 'completed'}
        trades = [{'trade_number': 1}]
        conn = FakeConnection(rows=[saved], trades=trades)

        found = get_run(conn, run_id=1000)

        self.assertEqual(found['trades'], trades)
        self.assertIs(conn.row_factory, dict_row)
        self.assertEqual(conn.events[0], 'begin')
        self.assertEqual(conn.events[-1], 'commit')
        statements = [event for event in conn.committed if event[0] == 'execute']
        self.assertEqual(len(statements), 3)
        self.assertEqual(statements[0][1],
            'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        self.assertIn('FROM backtest_runs AS r', statements[1][1])
        self.assertIn('FROM trades WHERE run_id', statements[2][1])
        self.assertEqual(statements[1][2], (1000,))
        self.assertEqual(statements[2][2], (1000,))

    def test_get_run_missing_row_exits_transaction_without_trade_query(self):
        conn = FakeConnection(rows=[None])

        self.assertIsNone(get_run(conn, run_id=9999))

        self.assertEqual(conn.events[-1], 'commit')
        self.assertEqual(len(conn.committed), 2)
        self.assertFalse(any('FROM trades' in event[1] for event in conn.committed))

    def test_get_run_rejects_an_existing_transaction(self):
        conn = FakeConnection()
        conn.info.transaction_status = TransactionStatus.INTRANS

        with self.assertRaisesRegex(ValueError, 'idle connection'):
            get_run(conn, run_id=1000)
        self.assertEqual(conn.events, [])


if __name__ == '__main__':
    unittest.main()
