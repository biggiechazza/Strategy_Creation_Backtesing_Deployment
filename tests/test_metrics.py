# Verify net-P&L metrics with small completed-trade sequences.
from __future__ import annotations
import unittest
from datetime import datetime, timedelta, timezone
from app.metrics import calculate_results
from app.models import PositionState, Trade

Start = datetime(2024, 6, 11, 14, 0, tzinfo=timezone.utc)


def make_trades(*net_pnls: float) -> tuple[Trade, ...]:
    '''Make cost-free trades with the requested net dollar results.'''
    return tuple(Trade(direction=PositionState.LONG,
            quantity=1,
            entry_timestamp=Start + timedelta(minutes=2 * index),
            entry_price=19000,
            exit_timestamp=Start + timedelta(minutes=2 * index + 1),
            exit_price=19000 + net_pnl / 20,
            gross_points=net_pnl / 20,
            gross_pnl=net_pnl,
            cost_dollars=0,
            net_pnl=net_pnl,)
        for index, net_pnl in enumerate(net_pnls))


class MetricsTests(unittest.TestCase):
    '''Check summary values against hand-calculated equity curves.'''

    def test_zero_trades_have_explicit_empty_result(self):
        result = calculate_results(())
        self.assertEqual(result.trades, ())
        self.assertEqual((result.total_trades, result.winning_trades,
            result.losing_trades), (0, 0, 0))
        self.assertEqual((result.win_rate, result.total_net_pnl,
            result.average_trade, result.max_drawdown), (0, 0, 0, 0))
        self.assertIsNone(result.profit_factor)

    def test_single_winner(self):
        trade = make_trades(100)[0]
        result = calculate_results([trade])
        self.assertEqual(result.trades, (trade,))
        self.assertEqual((result.total_trades, result.winning_trades,
            result.losing_trades), (1, 1, 0))
        self.assertEqual((result.win_rate, result.total_net_pnl,
            result.average_trade, result.max_drawdown), (1, 100, 100, 0))
        self.assertIsNone(result.profit_factor)

    def test_single_loser(self):
        result = calculate_results(make_trades(-100))
        self.assertEqual((result.total_trades, result.winning_trades,
            result.losing_trades), (1, 0, 1))
        self.assertEqual((result.win_rate, result.total_net_pnl,
            result.average_trade, result.profit_factor,
            result.max_drawdown), (0, -100, -100, 0, 100))

    def test_mixed_winners_losers_average_and_profit_factor(self):
        result = calculate_results(make_trades(100, -50, 25))
        self.assertEqual((result.total_trades, result.winning_trades,
            result.losing_trades), (3, 2, 1))
        self.assertAlmostEqual(result.win_rate, 2 / 3)
        self.assertEqual((result.total_net_pnl, result.average_trade,
            result.profit_factor, result.max_drawdown), (75, 25, 2.5, 50))

    def test_break_even_counts_only_toward_total(self):
        result = calculate_results(make_trades(100, 0, -50))
        self.assertEqual((result.total_trades, result.winning_trades,
            result.losing_trades), (3, 1, 1))
        self.assertAlmostEqual(result.win_rate, 1 / 3)
        self.assertEqual(result.total_net_pnl, 50)
        self.assertEqual(result.profit_factor, 2)

    def test_no_losing_trades_have_undefined_profit_factor(self):
        result = calculate_results(make_trades(100, 50))
        self.assertEqual(result.winning_trades, 2)
        self.assertEqual(result.losing_trades, 0)
        self.assertIsNone(result.profit_factor)

    def test_all_losing_trades_have_zero_profit_factor(self):
        result = calculate_results(make_trades(-100, -50))
        self.assertEqual(result.winning_trades, 0)
        self.assertEqual(result.losing_trades, 2)
        self.assertEqual(result.profit_factor, 0)
        self.assertEqual(result.max_drawdown, 150)

    def test_all_break_even_trades_have_undefined_profit_factor(self):
        result = calculate_results(make_trades(0, 0))
        self.assertEqual((result.total_trades, result.winning_trades,
            result.losing_trades), (2, 0, 0))
        self.assertEqual((result.win_rate, result.average_trade,
            result.total_net_pnl, result.max_drawdown), (0, 0, 0, 0))
        self.assertIsNone(result.profit_factor)

    def test_drawdown_from_known_sequence(self):
        # Equity: 0, 100, 60, -30, 170, 120; largest peak-to-trough fall is 130.
        result = calculate_results(make_trades(100, -40, -90, 200, -50))
        self.assertEqual(result.max_drawdown, 130)
        self.assertEqual(result.total_net_pnl, 120)

    def test_drawdown_starts_from_zero_before_first_loss(self):
        result = calculate_results(make_trades(-100, 25))
        self.assertEqual(result.max_drawdown, 100)

    def test_continuously_rising_equity_has_no_drawdown(self):
        result = calculate_results(make_trades(10, 20, 30))
        self.assertEqual(result.max_drawdown, 0)

    def test_metrics_use_net_pnl_instead_of_gross_pnl(self):
        trade = Trade(direction=PositionState.LONG,
            quantity=1,
            entry_timestamp=Start,
            entry_price=19000,
            exit_timestamp=Start + timedelta(minutes=1),
            exit_price=19003,
            gross_points=3,
            gross_pnl=60,
            cost_dollars=40,
            net_pnl=20,)
        result = calculate_results((trade,))
        self.assertEqual(result.total_net_pnl, 20)
        self.assertEqual(result.average_trade, 20)


if __name__ == '__main__':
    unittest.main()
