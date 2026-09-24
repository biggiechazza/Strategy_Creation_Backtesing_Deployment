from app.data_loader import load_nq_data
from app.engine import run_backtest
from app.metrics import calculate_results
from app.models import BacktestConfig, Strategy, Signal

# Placeholder user inputs will be though agent after deploy
# 
quantity = int(input('Enter the quantity: '))
cost_points_per_trade = float(input('Enter the cost per trade in points: '))

def main():
    bars = load_nq_data("nq_active_1min_backtestingpy.csv")
    configuration = BacktestConfig(quantity = quantity, cost_points_per_trade = cost_points_per_trade)
    strategy = # STRATEGY RULESET HERE (placeholder)
    trades = run_backtest(bars, strategy, configuration)
    result = calculate_results(trades)
    print(result)

if __name__ == "__main__":
    main()