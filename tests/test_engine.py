# Verify same-bar execution, position rules, P&L, and strategy visibility.
from __future__ import annotations
import unittest
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from app.engine import run_backtest
from app.models import BacktestConfig, Bar, PositionState, Signal, Trade

Start = datetime(2024, 6, 11, 14, 0, tzinfo=timezone.utc)


def make_bars(*closes: float) -> list[Bar]:
    '''Make completed bars whose open and close differ for fill checks.'''
    return [Bar(timestamp=Start + timedelta(minutes=index),
            open=close - 1,
            high=close + 2,
            low=close - 2,
            close=close,
            volume=100,
            contract='NQM4',
            trade_date=date(2024, 6, 11),)
        for index, close in enumerate(closes)]


class ScriptedStrategy:
    '''Return one known signal per bar and retain each prior-bar view.'''

    def __init__(self, signals: Sequence[Signal]):
        self.signals = signals
        self.evaluations = []

    def evaluate(self, current_bar: Bar, prior_bars: Sequence[Bar]) -> Signal:
        self.evaluations.append((current_bar, prior_bars))
        return self.signals[len(self.evaluations) - 1]


class EngineTests(unittest.TestCase):
    '''Check trades against manually calculable NQ bar sequences.'''

    def run_signals(self, closes: tuple[float, ...], signals: tuple[Signal, ...],
            quantity: int = 1, cost: float = 2.0
            ) -> tuple[list[Bar], ScriptedStrategy, tuple[Trade, ...]]:
        '''Run a scripted strategy over a matching set of closes.'''
        bars = make_bars(*closes)
        strategy = ScriptedStrategy(signals)
        trades = run_backtest(bars, strategy,
            BacktestConfig(quantity=quantity, cost_points_per_trade=cost))
        return bars, strategy, trades

    def test_buy_while_flat_opens_long_at_current_close(self):
        bars, _, trades = self.run_signals((19000, 19001, 19003),
            (Signal.BUY, Signal.HOLD, Signal.EXIT), quantity=2)
        self.assertEqual(len(trades), 1)
        trade = trades[0]
        self.assertIs(trade.direction, PositionState.LONG)
        self.assertEqual(trade.quantity, 2)
        self.assertEqual(trade.entry_timestamp, bars[0].timestamp)
        self.assertEqual(trade.entry_price, 19000)
        self.assertEqual(trade.exit_timestamp, bars[2].timestamp)
        self.assertEqual(trade.exit_price, 19003)
        self.assertNotEqual(trade.entry_price, bars[0].open)
        self.assertNotEqual(trade.entry_price, bars[1].close)
        self.assertNotEqual(trade.exit_price, bars[2].open)

    def test_sell_while_flat_opens_short_at_current_close(self):
        bars, _, trades = self.run_signals((19000, 18999, 18997),
            (Signal.SELL, Signal.HOLD, Signal.EXIT), quantity=3)
        self.assertEqual(len(trades), 1)
        trade = trades[0]
        self.assertIs(trade.direction, PositionState.SHORT)
        self.assertEqual(trade.quantity, 3)
        self.assertEqual(trade.entry_timestamp, bars[0].timestamp)
        self.assertEqual(trade.entry_price, bars[0].close)
        self.assertEqual(trade.exit_timestamp, bars[2].timestamp)
        self.assertEqual(trade.exit_price, bars[2].close)

    def test_exit_closes_long_and_allows_later_entry(self):
        bars, _, trades = self.run_signals((19000, 19003, 19005, 19007),
            (Signal.BUY, Signal.EXIT, Signal.BUY, Signal.EXIT))
        self.assertEqual(len(trades), 2)
        self.assertEqual((trades[0].entry_timestamp, trades[0].exit_timestamp),
            (bars[0].timestamp, bars[1].timestamp))
        self.assertEqual((trades[1].entry_timestamp, trades[1].exit_timestamp),
            (bars[2].timestamp, bars[3].timestamp))
        self.assertTrue(all(trade.direction is PositionState.LONG for trade in trades))

    def test_exit_closes_short_once(self):
        bars, _, trades = self.run_signals((19000, 18997),
            (Signal.SELL, Signal.EXIT))
        self.assertEqual(len(trades), 1)
        self.assertIs(trades[0].direction, PositionState.SHORT)
        self.assertEqual(trades[0].exit_timestamp, bars[1].timestamp)

    def test_buy_while_long_is_denied_without_changing_entry_or_quantity(self):
        bars, _, trades = self.run_signals((19000, 19002, 19003),
            (Signal.BUY, Signal.BUY, Signal.EXIT), quantity=5)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].entry_timestamp, bars[0].timestamp)
        self.assertEqual(trades[0].entry_price, 19000)
        self.assertEqual(trades[0].quantity, 5)
        self.assertEqual(trades[0].exit_timestamp, bars[2].timestamp)

    def test_sell_while_long_does_not_close_or_reverse(self):
        bars, _, trades = self.run_signals((19000, 18999, 19003),
            (Signal.BUY, Signal.SELL, Signal.EXIT))
        self.assertEqual(len(trades), 1)
        self.assertIs(trades[0].direction, PositionState.LONG)
        self.assertEqual(trades[0].entry_timestamp, bars[0].timestamp)
        self.assertEqual(trades[0].exit_timestamp, bars[2].timestamp)

    def test_sell_while_short_is_denied(self):
        bars, _, trades = self.run_signals((19000, 18999, 18997),
            (Signal.SELL, Signal.SELL, Signal.EXIT), quantity=4)
        self.assertEqual(len(trades), 1)
        self.assertIs(trades[0].direction, PositionState.SHORT)
        self.assertEqual(trades[0].entry_timestamp, bars[0].timestamp)
        self.assertEqual(trades[0].entry_price, 19000)
        self.assertEqual(trades[0].quantity, 4)

    def test_buy_while_short_does_not_close_or_reverse(self):
        bars, _, trades = self.run_signals((19000, 19001, 18997),
            (Signal.SELL, Signal.BUY, Signal.EXIT))
        self.assertEqual(len(trades), 1)
        self.assertIs(trades[0].direction, PositionState.SHORT)
        self.assertEqual(trades[0].entry_timestamp, bars[0].timestamp)
        self.assertEqual(trades[0].exit_timestamp, bars[2].timestamp)

    def test_only_one_position_exists_through_denied_signals_and_reentry(self):
        bars, _, trades = self.run_signals((19000, 19001, 19002, 19003, 19004, 19001),
            (Signal.BUY, Signal.SELL, Signal.BUY, Signal.EXIT,
            Signal.SELL, Signal.EXIT))
        self.assertEqual(len(trades), 2)
        self.assertEqual((trades[0].direction, trades[1].direction),
            (PositionState.LONG, PositionState.SHORT))
        self.assertEqual((trades[0].entry_timestamp, trades[0].exit_timestamp),
            (bars[0].timestamp, bars[3].timestamp))
        self.assertEqual((trades[1].entry_timestamp, trades[1].exit_timestamp),
            (bars[4].timestamp, bars[5].timestamp))

    def test_exit_while_flat_leaves_no_trade(self):
        _, _, trades = self.run_signals((19000, 19001),
            (Signal.EXIT, Signal.HOLD))
        self.assertEqual(trades, ())

    def test_hold_while_flat_leaves_no_trade(self):
        _, _, trades = self.run_signals((19000, 19001),
            (Signal.HOLD, Signal.HOLD))
        self.assertEqual(trades, ())

    def test_hold_preserves_open_long_and_short(self):
        for entry, exit_price in ((Signal.BUY, 19003), (Signal.SELL, 18997)):
            with self.subTest(entry=entry):
                bars, _, trades = self.run_signals((19000, 19001, 19002, exit_price),
                    (entry, Signal.HOLD, Signal.HOLD, Signal.EXIT))
                self.assertEqual(len(trades), 1)
                self.assertEqual(trades[0].entry_timestamp, bars[0].timestamp)
                self.assertEqual(trades[0].entry_price, 19000)
                self.assertEqual(trades[0].exit_timestamp, bars[3].timestamp)

    def test_long_winner_has_exact_points_dollars_and_cost(self):
        _, _, trades = self.run_signals((19000, 19003),
            (Signal.BUY, Signal.EXIT))
        self.assertEqual((trades[0].gross_points, trades[0].gross_pnl,
            trades[0].cost_dollars, trades[0].net_pnl), (3, 60, 40, 20))

    def test_long_loser_has_exact_points_and_dollars(self):
        _, _, trades = self.run_signals((19000, 18997),
            (Signal.BUY, Signal.EXIT))
        self.assertEqual((trades[0].gross_points, trades[0].gross_pnl,
            trades[0].cost_dollars, trades[0].net_pnl), (-3, -60, 40, -100))

    def test_short_winner_has_exact_points_and_dollars(self):
        _, _, trades = self.run_signals((19000, 18997),
            (Signal.SELL, Signal.EXIT))
        self.assertEqual((trades[0].gross_points, trades[0].gross_pnl,
            trades[0].cost_dollars, trades[0].net_pnl), (3, 60, 40, 20))

    def test_short_loser_has_exact_points_and_dollars(self):
        _, _, trades = self.run_signals((19000, 19003),
            (Signal.SELL, Signal.EXIT))
        self.assertEqual((trades[0].gross_points, trades[0].gross_pnl,
            trades[0].cost_dollars, trades[0].net_pnl), (-3, -60, 40, -100))

    def test_quantity_scales_gross_pnl_but_not_completed_trade_cost(self):
        _, _, trades = self.run_signals((19000, 19003),
            (Signal.BUY, Signal.EXIT), quantity=5)
        self.assertEqual((trades[0].gross_points, trades[0].gross_pnl,
            trades[0].cost_dollars, trades[0].net_pnl), (3, 300, 40, 260))

    def test_custom_cost_is_applied_once_even_for_multiple_contracts(self):
        _, _, trades = self.run_signals((19000, 19003),
            (Signal.BUY, Signal.EXIT), quantity=5, cost=1.5)
        self.assertEqual(trades[0].gross_pnl, 300)
        self.assertEqual(trades[0].cost_dollars, 30)
        self.assertEqual(trades[0].net_pnl, 270)

    def test_contract_change_discards_open_long_and_short(self):
        bars = make_bars(19607.75, 19855.75)
        bars[1] = replace(bars[1], contract='NQU4')
        for entry in (Signal.BUY, Signal.SELL):
            for action in (Signal.EXIT, Signal.HOLD):
                with self.subTest(entry=entry, action=action):
                    strategy = ScriptedStrategy((entry,))
                    next_strategy = ScriptedStrategy((action,))
                    trades = run_backtest(bars, strategy,
                        BacktestConfig(quantity=1, cost_points_per_trade=2.0),
                        strategy_factory=lambda: next_strategy)
                    self.assertEqual(trades, ())
                    self.assertEqual(len(strategy.evaluations), 1)
                    self.assertEqual(tuple(next_strategy.evaluations[0][1]), ())

    def test_contract_change_preserves_closed_trades_and_allows_new_entry(self):
        bars = make_bars(19000, 19003, 19005, 19250, 19253)
        bars[3:] = [replace(bar, contract='NQU4') for bar in bars[3:]]
        for entry, direction, net_pnl in ((Signal.BUY, PositionState.LONG, 20),
                (Signal.SELL, PositionState.SHORT, -100)):
            with self.subTest(entry=entry):
                strategy = ScriptedStrategy((Signal.BUY, Signal.EXIT, Signal.BUY))
                trades = run_backtest(bars, strategy,
                    BacktestConfig(quantity=1, cost_points_per_trade=2.0),
                    strategy_factory=lambda: ScriptedStrategy((entry, Signal.EXIT)))
                self.assertEqual(len(trades), 2)
                self.assertEqual((trades[0].entry_timestamp, trades[0].exit_timestamp),
                    (bars[0].timestamp, bars[1].timestamp))
                self.assertEqual(trades[0].net_pnl, 20)
                self.assertEqual((trades[1].entry_timestamp, trades[1].exit_timestamp),
                    (bars[3].timestamp, bars[4].timestamp))
                self.assertIs(trades[1].direction, direction)
                self.assertEqual((trades[1].entry_price, trades[1].cost_dollars,
                    trades[1].net_pnl), (19250, 40, net_pnl))

    def test_contract_change_while_flat_allows_new_trade(self):
        bars = make_bars(19000, 19250, 19253)
        bars[1:] = [replace(bar, contract='NQU4') for bar in bars[1:]]
        strategy = ScriptedStrategy((Signal.HOLD,))
        trades = run_backtest(bars, strategy,
            BacktestConfig(quantity=1, cost_points_per_trade=2.0),
            strategy_factory=lambda: ScriptedStrategy((Signal.BUY, Signal.EXIT)))
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].entry_timestamp, bars[1].timestamp)
        self.assertEqual(trades[0].net_pnl, 20)

    def test_contract_change_resets_stateful_strategy(self):
        class StatefulStrategy:
            def __init__(self, target: float):
                self.target = target
                self.entry_price = None

            def evaluate(self, current_bar, prior_bars):
                if self.entry_price is None:
                    self.entry_price = current_bar.close
                    return Signal.BUY
                if current_bar.close - self.entry_price >= self.target:
                    self.entry_price = None
                    return Signal.EXIT
                return Signal.HOLD

        bars = make_bars(19000, 19001, 19250, 19251, 19253)
        bars[2:] = [replace(bar, contract='NQU4') for bar in bars[2:]]
        trades = run_backtest(bars, StatefulStrategy(target=3),
            BacktestConfig(quantity=1, cost_points_per_trade=2.0),
            strategy_factory=lambda: StatefulStrategy(target=3))
        self.assertEqual(len(trades), 1)
        self.assertEqual((trades[0].entry_timestamp, trades[0].exit_timestamp),
            (bars[2].timestamp, bars[4].timestamp))
        self.assertEqual(trades[0].net_pnl, 20)

    def test_rollover_gap_cannot_trigger_momentum_and_old_views_stay_intact(self):
        class MomentumStrategy:
            def __init__(self):
                self.evaluations = []

            def evaluate(self, current_bar, prior_bars):
                self.evaluations.append((current_bar, prior_bars))
                if prior_bars and current_bar.close - prior_bars[-1].close >= 100:
                    return Signal.BUY
                return Signal.HOLD

        bars = make_bars(19606.75, 19607.75, 19855.75, 19856.75, 20104.75, 20105.75)
        bars[2:4] = [replace(bar, contract='NQU4') for bar in bars[2:4]]
        strategies = [MomentumStrategy()]

        def fresh_strategy():
            strategies.append(MomentumStrategy())
            return strategies[-1]

        trades = run_backtest(bars, strategies[0],
            BacktestConfig(quantity=1, cost_points_per_trade=2.0),
            strategy_factory=fresh_strategy)
        self.assertEqual(trades, ())
        self.assertEqual(len(strategies), 3)
        for index, strategy in enumerate(strategies):
            first, second = strategy.evaluations
            self.assertIs(first[0], bars[index * 2])
            self.assertEqual(tuple(first[1]), ())
            self.assertEqual(tuple(second[1]), (first[0],))
            self.assertTrue(all(bar.contract == second[0].contract for bar in second[1]))

    def test_contract_change_requires_factory_before_evaluating_new_bar(self):
        bars = make_bars(19000, 19250)
        bars[1] = replace(bars[1], contract='NQU4')
        for signal in (Signal.BUY, Signal.HOLD):
            with self.subTest(signal=signal):
                strategy = ScriptedStrategy((signal, Signal.HOLD))
                with self.assertRaisesRegex(ValueError, 'Contract changes require strategy_factory'):
                    run_backtest(bars, strategy,
                        BacktestConfig(quantity=1, cost_points_per_trade=2.0))
                self.assertEqual(len(strategy.evaluations), 1)

    def test_contract_change_rejects_reused_or_invalid_strategy(self):
        bars = make_bars(19000, 19250)
        bars[1] = replace(bars[1], contract='NQU4')
        for reuse in (True, False):
            with self.subTest(reuse=reuse):
                strategy = ScriptedStrategy((Signal.HOLD, Signal.HOLD))
                replacement = strategy if reuse else object()
                with self.assertRaisesRegex(ValueError, 'fresh strategy with evaluate'):
                    run_backtest(bars, strategy,
                        BacktestConfig(quantity=1, cost_points_per_trade=2.0),
                        strategy_factory=lambda: replacement)

    def test_contract_change_rejects_any_previously_used_strategy(self):
        bars = make_bars(19000, 19250, 19500)
        bars[1] = replace(bars[1], contract='NQU4')
        bars[2] = replace(bars[2], contract='NQZ4')
        first = ScriptedStrategy((Signal.HOLD, Signal.HOLD))
        second = ScriptedStrategy((Signal.HOLD,))
        replacements = iter((second, first))
        with self.assertRaisesRegex(ValueError, 'fresh strategy with evaluate'):
            run_backtest(bars, first,
                BacktestConfig(quantity=1, cost_points_per_trade=2.0),
                strategy_factory=lambda: next(replacements))
        self.assertEqual(len(first.evaluations), 1)
        self.assertEqual(len(second.evaluations), 1)

    def test_open_long_force_closes_at_final_bar_close(self):
        bars, _, trades = self.run_signals((19000, 19003),
            (Signal.BUY, Signal.HOLD))
        self.assertEqual(len(trades), 1)
        self.assertIs(trades[0].direction, PositionState.LONG)
        self.assertEqual(trades[0].exit_timestamp, bars[-1].timestamp)
        self.assertEqual(trades[0].exit_price, bars[-1].close)
        self.assertEqual(trades[0].net_pnl, 20)

    def test_open_short_force_closes_at_final_bar_close(self):
        bars, _, trades = self.run_signals((19000, 18997),
            (Signal.SELL, Signal.HOLD))
        self.assertEqual(len(trades), 1)
        self.assertIs(trades[0].direction, PositionState.SHORT)
        self.assertEqual(trades[0].exit_timestamp, bars[-1].timestamp)
        self.assertEqual(trades[0].exit_price, bars[-1].close)
        self.assertEqual(trades[0].net_pnl, 20)

    def test_final_bar_entry_creates_zero_movement_trade_with_cost(self):
        for entry in (Signal.BUY, Signal.SELL):
            with self.subTest(entry=entry):
                bars, _, trades = self.run_signals((19000, 19003),
                    (Signal.HOLD, entry))
                self.assertEqual(len(trades), 1)
                self.assertEqual(trades[0].entry_timestamp, bars[-1].timestamp)
                self.assertEqual(trades[0].exit_timestamp, bars[-1].timestamp)
                self.assertEqual(trades[0].entry_price, bars[-1].close)
                self.assertEqual(trades[0].exit_price, bars[-1].close)
                self.assertEqual((trades[0].gross_points, trades[0].gross_pnl,
                    trades[0].cost_dollars, trades[0].net_pnl), (0, 0, 40, -40))

    def test_empty_bars_raise_value_error(self):
        with self.assertRaises(ValueError):
            run_backtest([], ScriptedStrategy(()), BacktestConfig(quantity=1, cost_points_per_trade=2.0))

    def test_invalid_strategy_outputs_raise_type_error(self):
        for output in ('BUY', None, 1):
            with self.subTest(output=output):
                strategy = ScriptedStrategy((output,))
                with self.assertRaises(TypeError):
                    run_backtest(make_bars(19000), strategy,
                        BacktestConfig(quantity=1, cost_points_per_trade=2.0))
                self.assertEqual(len(strategy.evaluations), 1)

    def test_prior_bars_never_include_current_or_future_bars(self):
        bars = make_bars(19000, 19001, 19002, 19003)
        strategy = ScriptedStrategy((Signal.HOLD,) * len(bars))
        run_backtest(bars, strategy, BacktestConfig(quantity=1, cost_points_per_trade=2.0))
        for index, (current, prior) in enumerate(strategy.evaluations):
            with self.subTest(index=index):
                self.assertIs(current, bars[index])
                self.assertEqual(len(prior), index)
                self.assertEqual(tuple(prior), tuple(bars[:index]))
                self.assertNotIn(current, prior)
                self.assertTrue(all(bar not in prior for bar in bars[index + 1:]))
                with self.assertRaises(IndexError):
                    prior[index]

    def test_retained_prior_bar_views_do_not_grow_after_later_bars(self):
        bars = make_bars(19000, 19001, 19002, 19003)
        strategy = ScriptedStrategy((Signal.HOLD,) * len(bars))
        run_backtest(bars, strategy, BacktestConfig(quantity=1, cost_points_per_trade=2.0))
        early_prior = strategy.evaluations[1][1]
        self.assertEqual(len(early_prior), 1)
        self.assertEqual(tuple(early_prior), (bars[0],))
        with self.assertRaises(IndexError):
            early_prior[1]

    def test_strategy_programming_error_propagates(self):
        class BrokenStrategy:
            def evaluate(self, current_bar: Bar, prior_bars: Sequence[Bar]) -> Signal:
                raise RuntimeError('strategy failed')

        with self.assertRaisesRegex(RuntimeError, 'strategy failed'):
            run_backtest(make_bars(19000), BrokenStrategy(),
                BacktestConfig(quantity=1, cost_points_per_trade=2.0))

    def test_repeated_runs_do_not_share_positions_trades_or_prior_bars(self):
        bars = make_bars(19000, 19003)
        config = BacktestConfig(quantity=1, cost_points_per_trade=2.0)
        first = ScriptedStrategy((Signal.BUY, Signal.EXIT))
        second = ScriptedStrategy((Signal.BUY, Signal.EXIT))
        first_trades = run_backtest(bars, first, config)
        second_trades = run_backtest(bars, second, config)
        self.assertEqual(first_trades, second_trades)
        self.assertEqual(len(first_trades), 1)
        self.assertEqual(len(second_trades), 1)
        self.assertEqual(tuple(first.evaluations[0][1]), ())
        self.assertEqual(tuple(second.evaluations[0][1]), ())
        self.assertEqual(tuple(second.evaluations[1][1]), (bars[0],))


if __name__ == '__main__':
    unittest.main()
