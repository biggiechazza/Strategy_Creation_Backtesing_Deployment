# Strategy signals become completed trades here, with fills at bar closes

from __future__ import annotations
from collections.abc import Callable, Sequence
from app.models import NQ_POINT_VALUE, BacktestConfig, Bar, Position, PositionState, Signal, Strategy, Trade


# Strategies get a read only view of earlier bars from this contract segment
# Holding the backing list avoids copying the whole history on every evaluation
class _PriorBars(Sequence[Bar]):
    def __init__(self, bars: list[Bar]):
        self._bars = bars
    # Remember this view's length so holding onto it never exposes later bars through the sequence
        self._length = len(bars)

    def __len__(self) -> int:
        return self._length

    # Indexes and slices use the history visible when this view was created
    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(self._bars[i] for i in range(*index.indices(self._length)))
        if not isinstance(index, int):
            raise TypeError('Bar index must be an integer or slice')

        # Negative indexes count back from the saved length, even after the backing list grows
        if index < 0:
            index += self._length
        if not 0 <= index < self._length:
            raise IndexError('Bar index out of range')
        return self._bars[index]


# Closing a position creates the Trade, including its point result, dollar result, and cost
def _close_position(position: Position, bar: Bar, config: BacktestConfig) -> Trade:
    if position.direction is PositionState.LONG:
        gross_points = bar.close - position.entry_price
    else:
        gross_points = position.entry_price - bar.close
    gross_pnl = gross_points * NQ_POINT_VALUE * position.quantity

    # The fixed charge is per trade, adding contracts does not multiply it
    cost_dollars = config.cost_points_per_trade * NQ_POINT_VALUE
    return Trade(direction=position.direction,
            quantity=position.quantity,
            entry_timestamp=position.entry_timestamp,
            entry_price=position.entry_price,
            exit_timestamp=bar.timestamp,
            exit_price=bar.close,
            gross_points=gross_points,
            gross_pnl=gross_pnl,
            cost_dollars=cost_dollars,
            net_pnl=gross_pnl - cost_dollars,)


# The engine expects valid bars in time order, it does not repeat the CSV checks
# The result is a tuple of completed trades, rollover needs a factory that creates fresh instances
# Empty bars or invalid rollover setup raises ValueError, non Signal results raise TypeError
# Errors inside the strategy or factory still pass back to the caller
def run_backtest(bars: Sequence[Bar], strategy: Strategy,
    config: BacktestConfig, *, strategy_factory: Callable[[], Strategy] | None = None) -> tuple[Trade, ...]:
    if not bars:
        raise ValueError('Backtest requires at least one Bar')
    trades = []
    prior_bars = []
    used_strategies = [strategy]
    position = None
    for bar in bars:
        # Indicators and setup flags must start fresh when the contract changes
        # A new session date alone does not reset them
        if prior_bars and bar.contract != prior_bars[-1].contract:
            if strategy_factory is None:
                raise ValueError('Contract changes require strategy_factory to create a fresh strategy')
            next_strategy = strategy_factory()

            # Reusing any earlier instance would bring its old state back
            if (any(next_strategy is previous for previous in used_strategies)
                    or not callable(getattr(next_strategy, 'evaluate', None))):
                raise ValueError('strategy_factory must return a fresh strategy with evaluate()')
            strategy = next_strategy
            used_strategies.append(strategy)

            # Prices across contracts can jump, exclude the open trade so that gap does not enter P&L
            position = None

            # Old views still reference the previous list, replace it instead of clearing it in place
            prior_bars = []

        # The current bar joins history after evaluation, so the strategy only sees earlier bars there
        signal = strategy.evaluate(bar, _PriorBars(prior_bars))
        if not isinstance(signal, Signal):
            raise TypeError(f'Strategy returned an invalid Signal: {signal!r}')

        # Fills use this completed bar's close
        # Further entry signals are ignored while positioned, EXIT closes without reversing
        if position is None:
            if signal is Signal.BUY or signal is Signal.SELL:
                direction = PositionState.LONG if signal is Signal.BUY else PositionState.SHORT
                position = Position(direction=direction,
                        quantity=config.quantity,
                        entry_timestamp=bar.timestamp,
                        entry_price=bar.close,)
        elif signal is Signal.EXIT:
            trades.append(_close_position(position, bar, config))
            position = None

        prior_bars.append(bar)

    # Finish an open trade at the last close, even if it only opened on that final bar
    # A trade with no price movement still pays the configured cost
    if position is not None:
        trades.append(_close_position(position, bar, config))
    return tuple(trades)
