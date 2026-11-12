# Verify a tiny CSV passes through loading, execution, and result calculation.
from __future__ import annotations
import csv
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from app.data_loader import load_nq_data
from app.engine import run_backtest
from app.metrics import calculate_results
from app.models import BacktestConfig, Bar, PositionState, Signal


class TimedStrategy:
    '''Buy at 10:00 and exit at 10:02 using completed bar times.'''

    def evaluate(self, current_bar: Bar, prior_bars: Sequence[Bar]) -> Signal:
        if current_bar.timestamp.minute == 0:
            return Signal.BUY
        if current_bar.timestamp.minute == 2:
            return Signal.EXIT
        return Signal.HOLD


class IntegrationTests(unittest.TestCase):
    '''Check the complete Week 1 calculation on three CSV rows.'''

    def test_csv_to_trades_to_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'nq.csv'
            with path.open('w', encoding='utf-8', newline='') as target:
                writer = csv.DictWriter(target, fieldnames=(
                    'Date', 'Open', 'High', 'Low', 'Close', 'Volume',
                    'contract', 'trade_date'))
                writer.writeheader()
                for minute, close in ((0, 19000), (1, 19001), (2, 19003)):
                    writer.writerow({'Date': f'2024-06-11T10:{minute:02d}:00-04:00',
                        'Open': close - 1, 'High': close + 2, 'Low': close - 2,
                        'Close': close, 'Volume': 100, 'contract': 'NQM4',
                        'trade_date': '2024-06-11'})
            bars = load_nq_data(path)

        trades = run_backtest(bars, TimedStrategy(),
            BacktestConfig(quantity=1, cost_points_per_trade=2.0))
        result = calculate_results(trades)
        self.assertEqual(len(bars), 3)
        self.assertEqual(len(trades), 1)
        trade = result.trades[0]
        self.assertIs(trade.direction, PositionState.LONG)
        self.assertEqual(trade.entry_timestamp, bars[0].timestamp)
        self.assertEqual(trade.exit_timestamp, bars[2].timestamp)
        self.assertEqual((trade.entry_price, trade.exit_price), (19000, 19003))
        self.assertEqual((trade.gross_points, trade.gross_pnl,
            trade.cost_dollars, trade.net_pnl), (3, 60, 40, 20))
        self.assertEqual((result.total_trades, result.winning_trades,
            result.total_net_pnl, result.win_rate), (1, 1, 20, 1))


if __name__ == '__main__':
    unittest.main()
