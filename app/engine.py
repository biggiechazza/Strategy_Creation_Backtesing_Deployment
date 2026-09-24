# Execute strategy signals against completed NQ bars and return completed trades.
from __future__ import annotations
from collections.abc import Sequence
from app.models import NQ_POINT_VALUE, BacktestConfig, Bar, Position, PositionState, Signal, Strategy, Trade

class _PriorBars(Sequence[Bar]):
    '''Expose only the bars completed before one strategy evaluation.'''

    def __init__(self, bars: list[Bar]):
        self._bars = bars
        self._length = len(bars)

    def __len__(self) -> int:
        return self._length

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(self._bars[i] for i in range(*index.indices(self._length)))
        if not isinstance(index, int):
            raise TypeError('Bar index must be an integer or slice')
        if index < 0:
            index += self._length
        if not 0 <= index < self._length:
            raise IndexError('Bar index out of range')
        return self._bars[index]

def _close_position(position: Position, bar: Bar, config: BacktestConfig) -> Trade:
    '''Calculate one completed trade at the current bar close.'''

    if position.direction is PositionState.LONG:
        gross_points = bar.close - position.entry_price
    else:
        gross_points = position.entry_price - bar.close
    gross_pnl = gross_points * NQ_POINT_VALUE * position.quantity
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

# PRIMARY FUNCTION
def run_backtest(bars: Sequence[Bar], strategy: Strategy,
    config: BacktestConfig) -> tuple[Trade, ...]:
    '''Evaluate each completed bar, execute at its close, and return all trades.'''

    if not bars:
        raise ValueError('Backtest requires at least one Bar')
    trades = []
    prior_bars = []
    position = None
    for bar in bars:
        signal = strategy.evaluate(bar, _PriorBars(prior_bars))
        if not isinstance(signal, Signal):
            raise TypeError(f'Strategy returned an invalid Signal: {signal!r}')
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
    if position is not None:
        trades.append(_close_position(position, bar, config))
    return tuple(trades)
