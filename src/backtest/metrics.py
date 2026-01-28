"""
Performance metrics calculation for backtesting.
"""

from typing import List, Dict
import math


def calculate_metrics(portfolio) -> dict:
    """
    Calculate performance metrics from a VirtualPortfolio.

    Args:
        portfolio: VirtualPortfolio instance with history

    Returns:
        Dictionary of performance metrics
    """
    history = portfolio.portfolio_history
    transactions = portfolio.transactions

    if not history:
        return {"error": "No portfolio history available"}

    # Basic metrics
    starting_value = portfolio.starting_cash
    final_value = history[-1]["total_value"] if history else starting_value

    total_return = (final_value - starting_value) / starting_value
    total_return_pct = total_return * 100

    # Calculate max drawdown
    max_drawdown = calculate_max_drawdown(history)

    # Calculate trade statistics
    trade_stats = calculate_trade_stats(transactions)

    # Calculate daily returns for additional metrics
    daily_returns = calculate_daily_returns(history)

    # Sharpe ratio (simplified, assuming 0% risk-free rate)
    sharpe_ratio = calculate_sharpe_ratio(daily_returns)

    return {
        "starting_value": round(starting_value, 2),
        "final_value": round(final_value, 2),
        "total_return": round(total_return, 4),
        "total_return_pct": round(total_return_pct, 2),
        "max_drawdown": round(max_drawdown, 4),
        "max_drawdown_pct": round(max_drawdown * 100, 2),
        "sharpe_ratio": round(sharpe_ratio, 2) if sharpe_ratio else None,
        "total_trades": trade_stats["total_trades"],
        "buy_trades": trade_stats["buy_trades"],
        "sell_trades": trade_stats["sell_trades"],
        "winning_trades": trade_stats["winning_trades"],
        "losing_trades": trade_stats["losing_trades"],
        "win_rate": round(trade_stats["win_rate"], 2) if trade_stats["win_rate"] else None,
        "total_profit": round(trade_stats["total_profit"], 2),
        "total_fees": round(trade_stats["total_fees"], 2),
        "trading_days": len(history)
    }


def calculate_max_drawdown(history: List[dict]) -> float:
    """
    Calculate maximum drawdown from portfolio history.

    Args:
        history: List of daily portfolio snapshots

    Returns:
        Maximum drawdown as decimal (0.1 = 10%)
    """
    if not history:
        return 0.0

    peak = history[0]["total_value"]
    max_drawdown = 0.0

    for snapshot in history:
        value = snapshot["total_value"]
        if value > peak:
            peak = value
        drawdown = (peak - value) / peak if peak > 0 else 0
        max_drawdown = max(max_drawdown, drawdown)

    return max_drawdown


def calculate_trade_stats(transactions: List[dict]) -> dict:
    """
    Calculate trading statistics.

    Args:
        transactions: List of transaction records

    Returns:
        Trade statistics dictionary
    """
    buy_trades = [t for t in transactions if t["type"] == "buy"]
    sell_trades = [t for t in transactions if t["type"] == "sell"]

    # Calculate winning/losing trades (from sells)
    winning = [t for t in sell_trades if t.get("profit_loss", 0) > 0]
    losing = [t for t in sell_trades if t.get("profit_loss", 0) < 0]

    total_profit = sum(t.get("profit_loss", 0) for t in sell_trades)
    total_fees = sum(t.get("fee", 0) for t in transactions)

    win_rate = None
    if sell_trades:
        win_rate = len(winning) / len(sell_trades) * 100

    return {
        "total_trades": len(transactions),
        "buy_trades": len(buy_trades),
        "sell_trades": len(sell_trades),
        "winning_trades": len(winning),
        "losing_trades": len(losing),
        "win_rate": win_rate,
        "total_profit": total_profit,
        "total_fees": total_fees
    }


def calculate_daily_returns(history: List[dict]) -> List[float]:
    """
    Calculate daily returns from portfolio history.

    Args:
        history: List of daily portfolio snapshots

    Returns:
        List of daily returns
    """
    if len(history) < 2:
        return []

    returns = []
    for i in range(1, len(history)):
        prev_value = history[i - 1]["total_value"]
        curr_value = history[i]["total_value"]
        if prev_value > 0:
            daily_return = (curr_value - prev_value) / prev_value
            returns.append(daily_return)

    return returns


def calculate_sharpe_ratio(daily_returns: List[float], risk_free_rate: float = 0.0) -> float:
    """
    Calculate Sharpe ratio from daily returns.

    Args:
        daily_returns: List of daily returns
        risk_free_rate: Annual risk-free rate (default 0)

    Returns:
        Annualized Sharpe ratio
    """
    if len(daily_returns) < 2:
        return None

    # Calculate average daily return
    avg_return = sum(daily_returns) / len(daily_returns)

    # Calculate standard deviation of daily returns
    variance = sum((r - avg_return) ** 2 for r in daily_returns) / len(daily_returns)
    std_dev = math.sqrt(variance)

    if std_dev == 0:
        return None

    # Daily risk-free rate
    daily_rf = risk_free_rate / 252

    # Sharpe ratio (annualized)
    sharpe = (avg_return - daily_rf) / std_dev * math.sqrt(252)

    return sharpe


def format_report(metrics: dict, portfolio) -> str:
    """
    Format a human-readable backtest report.

    Args:
        metrics: Metrics dictionary from calculate_metrics
        portfolio: VirtualPortfolio instance

    Returns:
        Formatted report string
    """
    lines = [
        "=" * 60,
        "BACKTEST RESULTS",
        "=" * 60,
        "",
        "PERFORMANCE SUMMARY",
        "-" * 40,
        f"Starting Value:     ${metrics['starting_value']:,.2f}",
        f"Final Value:        ${metrics['final_value']:,.2f}",
        f"Total Return:       {metrics['total_return_pct']:+.2f}%",
        f"Max Drawdown:       {metrics['max_drawdown_pct']:.2f}%",
        f"Sharpe Ratio:       {metrics['sharpe_ratio'] or 'N/A'}",
        f"Trading Days:       {metrics['trading_days']}",
        "",
        "TRADE STATISTICS",
        "-" * 40,
        f"Total Trades:       {metrics['total_trades']}",
        f"  Buy Orders:       {metrics['buy_trades']}",
        f"  Sell Orders:      {metrics['sell_trades']}",
        f"Winning Trades:     {metrics['winning_trades']}",
        f"Losing Trades:      {metrics['losing_trades']}",
        f"Win Rate:           {metrics['win_rate'] or 'N/A'}%",
        f"Total Profit/Loss:  ${metrics['total_profit']:+,.2f}",
        f"Total Fees:         ${metrics['total_fees']:.2f}",
        "",
    ]

    # Add final holdings
    if portfolio.holdings:
        lines.extend([
            "FINAL HOLDINGS",
            "-" * 40,
        ])
        for symbol, qty in portfolio.holdings.items():
            avg_cost = portfolio.average_cost.get(symbol, 0)
            lines.append(f"  {symbol}: {qty:.4f} shares @ ${avg_cost:.2f} avg")
        lines.append(f"  Cash: ${portfolio.cash:,.2f}")
        lines.append("")

    # Add recent transactions
    if portfolio.transactions:
        lines.extend([
            "TRANSACTION LOG (Last 10)",
            "-" * 40,
        ])
        for t in portfolio.transactions[-10:]:
            t_type = t["type"].upper()
            lines.append(
                f"  {t['date']} | {t_type:4} | {t['symbol']:5} | "
                f"{t['quantity']:.4f} @ ${t['price']:.2f}"
            )
        lines.append("")

    lines.append("=" * 60)

    return "\n".join(lines)
