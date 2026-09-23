# Summarize completed NQ trades into the shared BacktestResult model.
from __future__ import annotations
from collections.abc import Sequence
from app.models import BacktestResult, Trade

def calculate_results(trades: Sequence[Trade]) -> BacktestResult:
    '''Calculate net-P&L metrics from completed trades in engine order.
    Break-even trades count toward total trades. Profit factor is undefined
    without losses, and drawdown starts from a zero-dollar peak.'''
    completed_trades = tuple(trades)
    winning_trades = 0
    losing_trades = 0
    gross_profit = 0.0
    gross_loss = 0.0
    cumulative_pnl = 0.0
    peak = 0.0
    max_drawdown = 0.0

    for trade in completed_trades:
        net_pnl = trade.net_pnl
        cumulative_pnl += net_pnl
        if net_pnl > 0:
            winning_trades += 1
            gross_profit += net_pnl
        elif net_pnl < 0:
            losing_trades += 1
            gross_loss -= net_pnl
        peak = max(peak, cumulative_pnl)
        max_drawdown = max(max_drawdown, peak - cumulative_pnl)

    total_trades = len(completed_trades)
    return BacktestResult(trades=completed_trades,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=winning_trades / total_trades if total_trades else 0.0,
            total_net_pnl=cumulative_pnl,
            average_trade=cumulative_pnl / total_trades if total_trades else 0.0,
            profit_factor=gross_profit / gross_loss if gross_loss else None,
            max_drawdown=max_drawdown,)
