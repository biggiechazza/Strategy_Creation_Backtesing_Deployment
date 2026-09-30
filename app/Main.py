# Run and retrieve saved backtests from the terminal.
import argparse
import hashlib
from getpass import getpass
from pathlib import Path
from pprint import pprint
from app.data_loader import load_nq_data_with_hash
from app.engine import run_backtest
from app.metrics import calculate_results
from app.models import BacktestConfig
from app.strategy_loader import load_strategy
from database.connection import connect_database
from database.run_repository import complete_run, fail_run, get_run, start_run
from database.strategy_repository import get_strategy_version

TEST_DATABASE = 'strategy_app_test'
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET = PROJECT_ROOT / 'nq_active_1min_backtestingpy.csv'
ENGINE_FILES = ('data_loader.py', 'engine.py', 'metrics.py', 'models.py')


def sha256_engine() -> str:
    '''Hash the engine files used for this run.'''
    digest = hashlib.sha256()
    for name in ENGINE_FILES:
        digest.update(name.encode('utf-8') + b'\0')
        with (PROJECT_ROOT / 'app' / name).open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
    return digest.hexdigest()


def run(conn, *, version_id: int, quantity: int, cost_points: float) -> int:
    config = BacktestConfig(quantity=quantity, cost_points_per_trade=cost_points)
    version = get_strategy_version(conn, version_id=version_id)
    run_id = start_run(conn, strategy_version_id=version_id, config=config,
        dataset_ref=DATASET.name)
    print(f'Started run {run_id} (saved as running).')
    try:
        strategy = load_strategy(version)
        bars, dataset_hash = load_nq_data_with_hash(DATASET)
        engine_hash = sha256_engine()
        trades = run_backtest(bars, strategy, config)
        result = calculate_results(trades)
        complete_run(conn, run_id=run_id, result=result,
            dataset_sha256=dataset_hash, engine_sha256=engine_hash)
    except Exception as error:
        try:
            fail_run(conn, run_id=run_id,
                error_message=f'{type(error).__name__}: {error}'[:1000])
        except Exception as status_error:
            print(f'Could not mark run {run_id} failed: {status_error}')
        raise
    print(f'Completed run {run_id}: {result.total_trades} trades; '
        f'net P&L ${result.total_net_pnl:.2f}')
    return run_id


def main() -> int:
    parser = argparse.ArgumentParser(description='Backtests in strategy_app_test')
    commands = parser.add_subparsers(dest='command', required=True)
    run_command = commands.add_parser('run', help='Run and save a strategy version')
    run_command.add_argument('version_id', type=int)
    run_command.add_argument('--quantity', type=int, default=1)
    run_command.add_argument('--cost-points', type=float, required=True)
    show_command = commands.add_parser('show', help='Retrieve a saved execution')
    show_command.add_argument('run_id', type=int)
    args = parser.parse_args()

    password = getpass('PostgreSQL password for postgres: ')
    with connect_database(database=TEST_DATABASE, user='postgres', password=password) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT current_database()')
            actual_database = cur.fetchone()[0]
        if actual_database != TEST_DATABASE:
            raise RuntimeError(f'Refusing to use database {actual_database!r}')
        if args.command == 'run':
            run(conn, version_id=args.version_id, quantity=args.quantity,
                cost_points=args.cost_points)
        else:
            saved = get_run(conn, run_id=args.run_id)
            if saved is None:
                print(f'Run {args.run_id} was not found in {TEST_DATABASE}.')
                return 1
            pprint(saved, sort_dicts=False)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
