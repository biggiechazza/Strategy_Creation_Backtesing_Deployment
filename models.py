"""Shared domain contracts for the Week 1 NQ backtesting engine.

This module intentionally contains data definitions only. CSV parsing, market-data
validation, signal execution, P&L calculations, and performance metrics belong to
their respective application modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from math import isfinite
from typing import Final, Protocol, Sequence


NQ_TICK_SIZE: Final[float] = 0.25
"""Minimum NQ price movement, measured in index points."""

NQ_TICK_VALUE: Final[float] = 5.0
"""Dollar value of one NQ tick for one contract."""

NQ_POINT_VALUE: Final[float] = 20.0
"""Dollar value of one full NQ point for one contract."""


class Signal(str, Enum):
    """An action a strategy may request from the execution engine."""

    BUY = "buy"
    SELL = "sell"
    EXIT = "exit"
    HOLD = "hold"


class PositionState(str, Enum):
    """The engine's possible single-position states."""

    FLAT = "flat"
    LONG = "long"
    SHORT = "short"


def _validate_quantity(quantity: int) -> None:
    """Raise a clear error unless ``quantity`` is an integer from 1 through 10."""

    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise TypeError("quantity must be an integer between 1 and 10")
    if not 1 <= quantity <= 10:
        raise ValueError("quantity must be between 1 and 10")


def _validate_open_direction(direction: PositionState) -> None:
    """Validate the direction used by an open position or completed trade."""

    if not isinstance(direction, PositionState):
        raise TypeError("direction must be a PositionState")
    if direction is PositionState.FLAT:
        raise ValueError("an open position or completed trade cannot be FLAT")


def _validate_cost_points(cost_points: float) -> None:
    """Reject costs that cannot represent a finite, non-negative point charge."""

    if isinstance(cost_points, bool) or not isinstance(cost_points, (int, float)):
        raise TypeError("cost_points_per_trade must be a real number")
    if not isfinite(cost_points) or cost_points < 0:
        raise ValueError("cost_points_per_trade must be finite and non-negative")


@dataclass(frozen=True, kw_only=True)
class Bar:
    """One completed NQ market bar.

    ``timestamp`` is the source market-data timestamp. The loader should preserve
    its timezone information. ``trade_date`` is the supplied futures session date;
    it must not be inferred from ``timestamp.date()`` because a futures session can
    cross midnight.

    Structural OHLC and chronological validation deliberately belongs to the data
    layer rather than this representation.
    """

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    contract: str
    trade_date: date


@dataclass(frozen=True, kw_only=True)
class BacktestConfig:
    """Configuration shared by one NQ backtest run.

    The placeholder cost is measured in NQ points per completed trade. It is
    applied once per trade and does not scale with ``quantity``.
    """

    quantity: int
    cost_points_per_trade: float = 2.0

    def __post_init__(self) -> None:
        """Enforce the V1 quantity range and a usable point cost."""

        _validate_quantity(self.quantity)
        _validate_cost_points(self.cost_points_per_trade)


@dataclass(frozen=True, kw_only=True)
class Position:
    """The engine-owned state for one currently open position."""

    direction: PositionState
    quantity: int
    entry_timestamp: datetime
    entry_price: float

    def __post_init__(self) -> None:
        """Reject states that cannot represent an open V1 position."""

        _validate_open_direction(self.direction)
        _validate_quantity(self.quantity)


@dataclass(frozen=True, kw_only=True)
class Trade:
    """An auditable record of one completed NQ trade.

    Point and dollar results are supplied by the execution engine. This model does
    not recalculate or reconcile them so that calculation behavior remains in one
    well-tested engine boundary.
    """

    direction: PositionState
    quantity: int
    entry_timestamp: datetime
    entry_price: float
    exit_timestamp: datetime
    exit_price: float
    gross_points: float
    gross_pnl: float
    cost_dollars: float
    net_pnl: float

    def __post_init__(self) -> None:
        """Reject states that cannot represent a completed V1 trade."""

        _validate_open_direction(self.direction)
        _validate_quantity(self.quantity)


@dataclass(frozen=True, kw_only=True)
class BacktestResult:
    """Structured output from one completed backtest.

    P&L, average trade, and maximum drawdown are dollar amounts based on net P&L.
    ``win_rate`` is a fraction from 0.0 through 1.0. ``profit_factor`` may be
    ``None`` when the metrics layer defines the ratio as undefined for an edge
    case, such as a run with no trades.
    """

    trades: tuple[Trade, ...]
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_net_pnl: float
    average_trade: float
    profit_factor: float | None
    max_drawdown: float

    def __post_init__(self) -> None:
        """Keep the completed trade collection immutable with the result summary."""

        if not isinstance(self.trades, tuple):
            raise TypeError("trades must be a tuple of completed Trade objects")


class Strategy(Protocol):
    """Structural interface implemented by a sequential trading strategy.

    The engine calls ``evaluate`` only after ``current_bar`` is complete.
    ``prior_bars`` must contain bars evaluated before the current bar and must not
    contain the current bar or any future bar. A strategy returns intent only; it
    never mutates engine position, trade, or account state.
    """

    def evaluate(
        self,
        current_bar: Bar,
        prior_bars: Sequence[Bar],
    ) -> Signal:
        """Return the requested action for ``current_bar``."""

        ...


__all__ = [
    "NQ_POINT_VALUE",
    "NQ_TICK_SIZE",
    "NQ_TICK_VALUE",
    "BacktestConfig",
    "BacktestResult",
    "Bar",
    "Position",
    "PositionState",
    "Signal",
    "Strategy",
    "Trade",
]
