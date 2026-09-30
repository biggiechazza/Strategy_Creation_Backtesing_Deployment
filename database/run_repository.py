# Save and retrieve backtest runs and trades in PostgreSQL.
import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from app.models import BacktestConfig, BacktestResult


def start_run(conn: psycopg.Connection, *, strategy_version_id: int,
    config: BacktestConfig, dataset_ref: str) -> int:
    '''Commit a running record before reading the dataset.'''
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                '''
                INSERT INTO backtest_runs (
                    strategy_version_id, dataset_ref, quantity,
                    cost_points_per_trade
                )
                VALUES (%s, %s, %s, %s)
                RETURNING run_id
                ''',
                (
                    strategy_version_id,
                    dataset_ref,
                    config.quantity,
                    config.cost_points_per_trade,
                ),
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError('INSERT did not return a run_id')
            run_id = row[0]
    return run_id


def complete_run(conn: psycopg.Connection, *, run_id: int, result: BacktestResult,
    dataset_sha256: str, engine_sha256: str) -> None:
    '''Save trades, hashes, and metrics in one transaction.'''
    if result.total_trades != len(result.trades):
        raise ValueError('Result trade count does not match its trade list')

    with conn.transaction():
        with conn.cursor() as cur:
            cur.executemany(
                '''
                INSERT INTO trades (
                    run_id, trade_number, direction, quantity,
                    entry_timestamp, entry_utc_offset, entry_price,
                    exit_timestamp, exit_utc_offset, exit_price,
                    gross_points, gross_pnl, cost_dollars, net_pnl
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''',
                (
                    (
                        run_id, number, trade.direction.value, trade.quantity,
                        trade.entry_timestamp.replace(tzinfo=None),
                        trade.entry_timestamp.utcoffset(), trade.entry_price,
                        trade.exit_timestamp.replace(tzinfo=None),
                        trade.exit_timestamp.utcoffset(), trade.exit_price,
                        trade.gross_points, trade.gross_pnl,
                        trade.cost_dollars, trade.net_pnl,
                    )
                    for number, trade in enumerate(result.trades, start=1)
                ),
            )
            cur.execute(
                '''
                UPDATE backtest_runs SET
                    status = 'completed', finished_at = CURRENT_TIMESTAMP,
                    dataset_sha256 = %s, engine_sha256 = %s,
                    total_trades = %s, winning_trades = %s, losing_trades = %s,
                    win_rate = %s, total_net_pnl = %s, average_trade = %s,
                    profit_factor = %s, max_drawdown = %s
                WHERE run_id = %s AND status = 'running'
                RETURNING run_id
                ''',
                (
                    dataset_sha256, engine_sha256,
                    result.total_trades, result.winning_trades,
                    result.losing_trades, result.win_rate,
                    result.total_net_pnl, result.average_trade,
                    result.profit_factor, result.max_drawdown, run_id,
                ),
            )
            if cur.fetchone() is None:
                raise ValueError(f'Run {run_id} is missing or is not running')


def fail_run(conn: psycopg.Connection, *, run_id: int, error_message: str) -> None:
    '''Record failure after a failed save rolls back.'''
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                '''
                UPDATE backtest_runs SET
                    status = 'failed', finished_at = CURRENT_TIMESTAMP,
                    error_message = %s
                WHERE run_id = %s AND status = 'running'
                RETURNING run_id
                ''',
                (error_message, run_id),
            )
            if cur.fetchone() is None:
                raise ValueError(f'Run {run_id} is missing or is not running')


def get_run(conn: psycopg.Connection, *, run_id: int) -> dict | None:
    '''Retrieve a run and its trades from one repeatable-read snapshot.'''
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError('get_run requires an idle connection')
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            cur.execute(
                '''
                SELECT r.*, s.name AS strategy_name, v.version_number,
                       v.entrypoint_name, v.source_code
                FROM backtest_runs AS r
                JOIN strategy_versions AS v
                  ON v.strategy_version_id = r.strategy_version_id
                JOIN strategies AS s ON s.strategy_id = v.strategy_id
                WHERE r.run_id = %s
                ''',
                (run_id,),
            )
            run = cur.fetchone()
            if run is None:
                return None
            cur.execute(
                'SELECT * FROM trades WHERE run_id = %s ORDER BY trade_number',
                (run_id,),
            )
            run['trades'] = cur.fetchall()
            return run
