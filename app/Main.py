# Terminal commands for running backtests and looking up saved runs

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

# Find the CSV from the project folder, regardless of where the terminal was opened
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# TODO Add dataset path selection to the CLI
DATASET = PROJECT_ROOT / 'nq_active_1min_backtestingpy.csv'

ENGINE_FILES = ('data_loader.py', 'engine.py', 'metrics.py', 'models.py')


# The fingerprint covers these file names and bytes, including comments and line endings
# File errors pass back to the caller
def sha256_engine() -> str:
    digest = hashlib.sha256()
    for name in ENGINE_FILES:

        # Renaming a file should change the fingerprint even when its contents stay the same
        digest.update(name.encode('utf-8') + b'\0')
        with (PROJECT_ROOT / 'app' / name).open('rb') as source:

        # Chunks avoid holding another whole file in memory just to hash it
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
    return digest.hexdigest()


# Run a saved version w/ the selected settings and hand back its completed run ID
# main owns the connection, any error still goes back to the caller
def run(conn, *, version_id: int, quantity: int, cost_points: float) -> int:

    # Bad settings or a missing version should stop before a run is added to the DB
    config = BacktestConfig(quantity=quantity, cost_points_per_trade=cost_points)
    version = get_strategy_version(conn, version_id=version_id)

    # Strategy setup and CSV loading can fail, so record the run first
    run_id = start_run(conn, strategy_version_id=version_id, config=config, dataset_ref=DATASET.name)
    try:
        print(f'Started run {run_id} (saved as running).')
        strategy = load_strategy(version)

        # A separate hash pass could read a changed file, hash the bytes being parsed
        bars, dataset_hash = load_nq_data_with_hash(DATASET)

        engine_hash = sha256_engine()

        # Rollover needs a fresh instance so old indicators and setup flags do not carry over
        trades = run_backtest(bars, strategy, config,
            strategy_factory=lambda: load_strategy(version))

        result = calculate_results(trades)

        # Trades and summary belong in one transaction to avoid partial results
        complete_run(conn, run_id=run_id, result=result, dataset_sha256=dataset_hash, engine_sha256=engine_hash)

    # Ctrl+C should also get a chance to mark the run failed
    except BaseException as error:
        try:
        # Failure status is written separately because a completion save may have rolled back
            fail_run(conn, run_id=run_id, error_message=f'{type(error).__name__}: {error}'[:1000])
        except Exception as status_error:

            # If recording failure also breaks, preserve the error that actually stopped the run
            print(f'Could not mark run {run_id} failed: {status_error}')
        raise

    print(f'Completed run {run_id}: {result.total_trades} trades; 'f'net P&L ${result.total_net_pnl:.2f}')
    return run_id


# Run a selected strategy version or look up an earlier execution from the terminal
# run() uses a strategy version ID, show uses a saved run ID
# Successful commands return 0, a missing run returns 1, other errors pass through
def main() -> int:
    parser = argparse.ArgumentParser(description='Backtests in strategy_app_test')

    commands = parser.add_subparsers(dest='command', required=True)
    run_command = commands.add_parser('run', help='Run and save a strategy version')
    run_command.add_argument('version_id', type=int)
    run_command.add_argument('--quantity', type=int, default=1)

    # Cost free runs should be an explicit choice
    run_command.add_argument('--cost-points', type = float, required = True)
    show_command = commands.add_parser('show', help='Retrieve a saved execution')
    show_command.add_argument('run_id', type = int)
        # Intake collects a strategy file and saves it as a new strategy and version 1
    commands.add_parser('intake', help='Intake and save a Python strategy')

    args = parser.parse_args()

    password = getpass('PostgreSQL password for postgres: ')

    # Repo files handle each transaction, this block handles connection cleanup even on errors
    with connect_database(database=TEST_DATABASE, user='postgres', password=password) as conn:

        # Check the actual connection before any writes, a requested DB name is not enough
        with conn.cursor() as cur:
            cur.execute('SELECT current_database()')
            actual_database = cur.fetchone()[0]
        if actual_database != TEST_DATABASE:
            raise RuntimeError(f'Refusing to use database {actual_database!r}')

        if args.command == 'run':
            run(conn, version_id=args.version_id, quantity=args.quantity, cost_points=args.cost_points)

        elif args.command == 'intake':
            try:
                # Keep model setup out of commands that do not use intake
                from intake_agent_lc.Workflow import run_intake

                state = run_intake(conn)

            except (KeyboardInterrupt, EOFError):
                print('\nIntake cancelled.')

                # The write may have committed before the interruption
                print('If saving had started, check the database before retrying.')
                return 130
            except Exception as error:

                # Raw errors can contain provider or connection details, only show the type
                print(f'\nIntake stopped ({type(error).__name__}).')
                print('If saving had started, check the database before retrying.')
                return 1

            # Finishing the conversation does not guarantee the strategy was saved
            return 0 if state.get('save_status') == 'saved' else 1

        else:
        # A single snapshot prevents mismatched run and trade data during concurrent updates
            saved = get_run(conn, run_id = args.run_id)

            if saved is None:
                print(f'Run {args.run_id} was not found in {TEST_DATABASE}.')
                return 1
            pprint(saved, sort_dicts=False)

    return 0


if __name__ == '__main__':
    raise SystemExit(main())