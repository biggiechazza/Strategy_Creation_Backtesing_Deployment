# Summarize completed trades after the engine has applied costs

from __future__ import annotations
from collections.abc import Sequence
from math import isfinite
from app.models import BacktestResult, Trade


# NaN, infinity, and overflow cannot be stored as usable metrics
def _require_finite(value: float, name: str) -> None:
    try:
        if isfinite(value):
            return

    # isfinite may itself overflow on a huge integer
    except OverflowError:
        pass
    raise ValueError(f'{name} must be finite')


# Summarize the supplied trades in engine order using net dollar P&L
# win_rate is a fraction, and profit_factor stays None when there are no losses
# No trades means zero counts and dollar results, nonfinite calculations raise ValueError
def calculate_results(trades: Sequence[Trade]) -> BacktestResult:
    completed_trades = tuple(trades)
    winning_trades = 0
    losing_trades = 0
    gross_profit = 0.0
    gross_loss = 0.0
    cumulative_pnl = 0.0

    # An initial loss counts as drawdown, so the starting peak is zero
    peak = 0.0
    max_drawdown = 0.0

    # Individually finite trades can still overflow a running total
    for trade in completed_trades:
        net_pnl = trade.net_pnl
        _require_finite(net_pnl, 'Trade net P&L')
        cumulative_pnl += net_pnl
        _require_finite(cumulative_pnl, 'Cumulative net P&L')

        peak = max(peak, cumulative_pnl)
        drawdown = peak - cumulative_pnl
        _require_finite(drawdown, 'Drawdown')
        max_drawdown = max(max_drawdown, drawdown)

        if net_pnl > 0:
            winning_trades += 1
            gross_profit += net_pnl
            _require_finite(gross_profit, 'Gross profit')
        elif net_pnl < 0:
            losing_trades += 1
            gross_loss -= net_pnl
            _require_finite(gross_loss, 'Gross loss')

    # Breakeven trades remain in the totals used for win rate and average trade
    total_trades = len(completed_trades)

    # Without losses there is nothing to divide by, leave the ratio undefined
    profit_factor = gross_profit / gross_loss if gross_loss else None
    if profit_factor is not None:
        _require_finite(profit_factor, 'Profit factor')
    return BacktestResult(trades=completed_trades,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=winning_trades / total_trades if total_trades else 0.0,
            total_net_pnl=cumulative_pnl,
            average_trade=cumulative_pnl / total_trades if total_trades else 0.0,
            profit_factor=profit_factor,
            max_drawdown=max_drawdown,)
