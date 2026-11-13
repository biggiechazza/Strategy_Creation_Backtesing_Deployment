# The shared objects passed around the app and DB run repository
# Calculations and market data checks stay with the code that uses them

from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from math import isfinite
from typing import Final, Protocol, Sequence

# Tick size is in points, tick and point values are dollars for one contract
NQ_TICK_SIZE: Final = 0.25

NQ_TICK_VALUE: Final = 5.0

NQ_POINT_VALUE: Final = 20.0


# Each strategy evaluation sends one of these enum members to the engine
class Signal(str, Enum):
    BUY = 'buy'
    SELL = 'sell'
    EXIT = 'exit'
    HOLD = 'hold'


# Positions and trades need a direction, FLAT is not valid for either record
class PositionState(str, Enum):
    FLAT = 'flat'
    LONG = 'long'
    SHORT = 'short'


# Contract quantity is an int from 1 through 10
def _validate_quantity(quantity: int) -> None:
    # Python counts bool as int, but True is not a contract quantity
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise TypeError('quantity must be an integer between 1 and 10')
    if not 1 <= quantity <= 10:
        raise ValueError('quantity must be between 1 and 10')


# A position or trade needs a long or short direction, never FLAT
def _validate_open_direction(direction: PositionState) -> None:
    if not isinstance(direction, PositionState):
        raise TypeError('direction must be a PositionState')
    if direction is PositionState.FLAT:
        raise ValueError('an open position or completed trade cannot be FLAT')


# The point cost and its dollar conversion both need to stay finite and nonnegative
def _validate_cost_points(cost_points: float) -> None:
    if isinstance(cost_points, bool) or not isinstance(cost_points, (int, float)):
        raise TypeError('cost_points_per_trade must be a real number')
    try:
        # Multiplying a finite point cost by the NQ point value can still overflow
        valid = isfinite(cost_points) and cost_points >= 0 and isfinite(cost_points * NQ_POINT_VALUE)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError('cost_points_per_trade must be finite, non-negative, and produce a finite dollar cost')


# One completed bar, frozen after the loader supplies its values
# Futures sessions can cross midnight, so keep the supplied trade_date and timestamp offset
# Creating a Bar directly skips the loader's price and time order checks
@dataclass(frozen=True, kw_only=True)
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    contract: str
    trade_date: date


# Quantity is contracts, cost is NQ points charged once per completed trade
# More contracts do not increase that fixed cost
@dataclass(frozen=True, kw_only=True)
class BacktestConfig:
    quantity: int
    cost_points_per_trade: float

    # Direct callers need the same settings checks as terminal runs
    def __post_init__(self) -> None:
        _validate_quantity(self.quantity)
        _validate_cost_points(self.cost_points_per_trade)


# The engine's open position, with entry details and a quantity from 1 through 10
# Direction has to be LONG or SHORT because this record represents an actual open trade
@dataclass(frozen=True, kw_only=True)
class Position:
    direction: PositionState
    quantity: int
    entry_timestamp: datetime
    entry_price: float

    def __post_init__(self) -> None:
        _validate_open_direction(self.direction)
        _validate_quantity(self.quantity)


# The completed trade record, including points, dollars, and costs calculated by the engine
# Only direction and quantity are checked here, recalculating P&L would duplicate engine logic
@dataclass(frozen=True, kw_only=True)
class Trade:
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
        _validate_open_direction(self.direction)
        _validate_quantity(self.quantity)


# Completed trades and the metrics calculated from them
# P&L, average trade, and drawdown are net dollars, win_rate is a fraction
# With no losses, profit_factor is None
# Only the tuple container is checked here, the metrics code supplies and checks the numbers
@dataclass(frozen=True, kw_only=True)
class BacktestResult:
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
        if not isinstance(self.trades, tuple):
            raise TypeError('trades must be a tuple of completed Trade objects')


# The interface shared by strategies, type hints do not enforce the method body
# evaluate gets the completed current bar and earlier history from the same contract segment
# The engine owns fills and positions, an emitted signal does not confirm that a trade happened
class Strategy(Protocol):
    # History starts empty, and saved views do not grow as new bars arrive
    # Contract changes replace the instance, a session date change alone does not
    def evaluate(self,current_bar: Bar,prior_bars: Sequence[Bar],) -> Signal:
        ...

__all__ = [
    'NQ_POINT_VALUE',
    'NQ_TICK_SIZE',
    'NQ_TICK_VALUE',
    'BacktestConfig',
    'BacktestResult',
    'Bar',
    'Position',
    'PositionState',
    'Signal',
    'Strategy',
    'Trade',]
