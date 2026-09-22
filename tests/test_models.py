"""Focused tests for the shared backtesting domain contracts."""

from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError, is_dataclass
from datetime import date, datetime, timezone
from typing import Sequence

from app.models import (
    NQ_POINT_VALUE,
    NQ_TICK_SIZE,
    NQ_TICK_VALUE,
    BacktestConfig,
    BacktestResult,
    Bar,
    Position,
    PositionState,
    Signal,
    Strategy,
    Trade,
)


class HoldStrategy:
    """Small structural implementation used to exercise the Strategy protocol."""

    def evaluate(
        self,
        current_bar: Bar,
        prior_bars: Sequence[Bar],
    ) -> Signal:
        del current_bar, prior_bars
        return Signal.HOLD


class ModelContractTests(unittest.TestCase):
    """Verify construction and intrinsic validation of the shared models."""

    def setUp(self) -> None:
        self.timestamp = datetime(2024, 6, 11, 14, 0, tzinfo=timezone.utc)
        self.bar = Bar(
            timestamp=self.timestamp,
            open=19_000.0,
            high=19_002.0,
            low=18_999.0,
            close=19_001.25,
            volume=100,
            contract="NQM4",
            trade_date=date(2024, 6, 11),
        )

    def make_trade(self) -> Trade:
        """Return a manually checkable completed trade for model tests."""

        return Trade(
            direction=PositionState.LONG,
            quantity=2,
            entry_timestamp=self.timestamp,
            entry_price=19_001.25,
            exit_timestamp=datetime(
                2024,
                6,
                11,
                14,
                5,
                tzinfo=timezone.utc,
            ),
            exit_price=19_004.25,
            gross_points=3.0,
            gross_pnl=120.0,
            cost_dollars=40.0,
            net_pnl=80.0,
        )

    def test_nq_constants(self) -> None:
        self.assertEqual(NQ_TICK_SIZE, 0.25)
        self.assertEqual(NQ_TICK_VALUE, 5.0)
        self.assertEqual(NQ_POINT_VALUE, 20.0)

    def test_enum_members_and_values(self) -> None:
        self.assertEqual(
            {member.name: member.value for member in Signal},
            {"BUY": "buy", "SELL": "sell", "EXIT": "exit", "HOLD": "hold"},
        )
        self.assertEqual(
            {member.name: member.value for member in PositionState},
            {"FLAT": "flat", "LONG": "long", "SHORT": "short"},
        )

    def test_models_are_dataclasses_and_retain_fields(self) -> None:
        position = Position(
            direction=PositionState.LONG,
            quantity=2,
            entry_timestamp=self.timestamp,
            entry_price=self.bar.close,
        )
        trade = self.make_trade()

        for model in (Bar, BacktestConfig, Position, Trade, BacktestResult):
            with self.subTest(model=model.__name__):
                self.assertTrue(is_dataclass(model))

        self.assertEqual(self.bar.contract, "NQM4")
        self.assertEqual(position.entry_price, 19_001.25)
        self.assertEqual(trade.net_pnl, 80.0)

    def test_config_accepts_boundary_quantities(self) -> None:
        self.assertEqual(BacktestConfig(quantity=1).quantity, 1)
        self.assertEqual(BacktestConfig(quantity=10).quantity, 10)

    def test_config_uses_and_retains_trade_cost(self) -> None:
        self.assertEqual(BacktestConfig(quantity=1).cost_points_per_trade, 2.0)
        self.assertEqual(
            BacktestConfig(quantity=1, cost_points_per_trade=1.5).cost_points_per_trade,
            1.5,
        )

    def test_config_rejects_out_of_range_quantities(self) -> None:
        for quantity in (0, 11):
            with self.subTest(quantity=quantity):
                with self.assertRaises(ValueError):
                    BacktestConfig(quantity=quantity)

    def test_config_rejects_non_integer_quantities(self) -> None:
        for quantity in (True, 1.5, "2"):
            with self.subTest(quantity=quantity):
                with self.assertRaises(TypeError):
                    BacktestConfig(quantity=quantity)  # type: ignore[arg-type]

    def test_config_rejects_invalid_trade_costs(self) -> None:
        for cost in (-0.25, float("nan"), float("inf")):
            with self.subTest(cost=cost):
                with self.assertRaises(ValueError):
                    BacktestConfig(quantity=1, cost_points_per_trade=cost)

        for cost in (True, "2.0"):
            with self.subTest(cost=cost):
                with self.assertRaises(TypeError):
                    BacktestConfig(  # type: ignore[arg-type]
                        quantity=1,
                        cost_points_per_trade=cost,
                    )

    def test_position_and_trade_cannot_be_flat(self) -> None:
        with self.assertRaises(ValueError):
            Position(
                direction=PositionState.FLAT,
                quantity=1,
                entry_timestamp=self.timestamp,
                entry_price=19_000.0,
            )

        trade_arguments = {
            "direction": PositionState.FLAT,
            "quantity": 1,
            "entry_timestamp": self.timestamp,
            "entry_price": 19_000.0,
            "exit_timestamp": self.timestamp,
            "exit_price": 19_000.0,
            "gross_points": 0.0,
            "gross_pnl": 0.0,
            "cost_dollars": 40.0,
            "net_pnl": -40.0,
        }
        with self.assertRaises(ValueError):
            Trade(**trade_arguments)  # type: ignore[arg-type]

    def test_backtest_result_retains_trades_and_metrics(self) -> None:
        trade = self.make_trade()
        result = BacktestResult(
            trades=(trade,),
            total_trades=1,
            winning_trades=1,
            losing_trades=0,
            win_rate=1.0,
            total_net_pnl=80.0,
            average_trade=80.0,
            profit_factor=None,
            max_drawdown=0.0,
        )

        self.assertEqual(result.trades, (trade,))
        self.assertEqual(result.total_net_pnl, 80.0)

    def test_backtest_result_requires_an_immutable_trade_tuple(self) -> None:
        with self.assertRaises(TypeError):
            BacktestResult(
                trades=[],  # type: ignore[arg-type]
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate=0.0,
                total_net_pnl=0.0,
                average_trade=0.0,
                profit_factor=None,
                max_drawdown=0.0,
            )

    def test_models_are_frozen_and_keyword_only(self) -> None:
        config = BacktestConfig(quantity=1)

        with self.assertRaises(FrozenInstanceError):
            config.quantity = 2  # type: ignore[misc]
        with self.assertRaises(TypeError):
            BacktestConfig(1)  # type: ignore[misc]

    def test_simple_strategy_satisfies_the_structural_contract(self) -> None:
        strategy: Strategy = HoldStrategy()

        self.assertIs(strategy.evaluate(self.bar, ()), Signal.HOLD)


if __name__ == "__main__":
    unittest.main()
