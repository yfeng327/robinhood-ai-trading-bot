"""
Virtual Portfolio for backtesting.
Tracks holdings, cash, and transaction history.
"""

from datetime import datetime
from typing import Dict, List, Optional


class VirtualPortfolio:
    """
    Simulates a trading portfolio for backtesting purposes.
    Tracks cash, holdings, and all transactions.
    """

    def __init__(self, starting_cash: float, transaction_fee: float = 0.0):
        """
        Initialize a virtual portfolio.

        Args:
            starting_cash: Initial cash amount
            transaction_fee: Fee per transaction as decimal (0.001 = 0.1%)
        """
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.transaction_fee = transaction_fee
        self.holdings: Dict[str, float] = {}  # symbol -> quantity
        self.average_cost: Dict[str, float] = {}  # symbol -> avg cost per share
        self.transactions: List[dict] = []
        self.portfolio_history: List[dict] = []  # Daily snapshots

    def buy(self, symbol: str, quantity: float, price: float, date: str) -> dict:
        """
        Execute a buy order.

        Args:
            symbol: Stock symbol
            quantity: Number of shares to buy
            price: Price per share
            date: Transaction date (YYYY-MM-DD)

        Returns:
            Transaction record or error dict
        """
        total_cost = quantity * price
        fee = total_cost * self.transaction_fee
        total_with_fee = total_cost + fee

        if total_with_fee > self.cash:
            return {
                "success": False,
                "error": f"Insufficient cash. Need ${total_with_fee:.2f}, have ${self.cash:.2f}"
            }

        # Update cash
        self.cash -= total_with_fee

        # Update holdings and average cost
        current_qty = self.holdings.get(symbol, 0)
        current_cost = self.average_cost.get(symbol, 0)

        if current_qty > 0:
            # Calculate new average cost
            total_value = (current_qty * current_cost) + total_cost
            new_qty = current_qty + quantity
            self.average_cost[symbol] = total_value / new_qty
        else:
            self.average_cost[symbol] = price

        self.holdings[symbol] = current_qty + quantity

        # Record transaction
        transaction = {
            "date": date,
            "type": "buy",
            "symbol": symbol,
            "quantity": quantity,
            "price": price,
            "total": total_cost,
            "fee": fee,
            "cash_after": self.cash
        }
        self.transactions.append(transaction)

        return {"success": True, "transaction": transaction}

    def sell(self, symbol: str, quantity: float, price: float, date: str) -> dict:
        """
        Execute a sell order.

        Args:
            symbol: Stock symbol
            quantity: Number of shares to sell
            price: Price per share
            date: Transaction date (YYYY-MM-DD)

        Returns:
            Transaction record or error dict
        """
        current_qty = self.holdings.get(symbol, 0)

        if quantity > current_qty:
            return {
                "success": False,
                "error": f"Insufficient shares. Want to sell {quantity}, have {current_qty}"
            }

        total_proceeds = quantity * price
        fee = total_proceeds * self.transaction_fee
        net_proceeds = total_proceeds - fee

        # Calculate profit/loss for this sale
        avg_cost = self.average_cost.get(symbol, 0)
        cost_basis = quantity * avg_cost
        profit_loss = net_proceeds - cost_basis

        # Update cash
        self.cash += net_proceeds

        # Update holdings
        new_qty = current_qty - quantity
        if new_qty <= 0:
            del self.holdings[symbol]
            if symbol in self.average_cost:
                del self.average_cost[symbol]
        else:
            self.holdings[symbol] = new_qty

        # Record transaction
        transaction = {
            "date": date,
            "type": "sell",
            "symbol": symbol,
            "quantity": quantity,
            "price": price,
            "total": total_proceeds,
            "fee": fee,
            "profit_loss": profit_loss,
            "cash_after": self.cash
        }
        self.transactions.append(transaction)

        return {"success": True, "transaction": transaction}

    def get_portfolio_value(self, prices: Dict[str, float]) -> float:
        """
        Calculate total portfolio value (cash + holdings).

        Args:
            prices: Current prices {symbol: price}

        Returns:
            Total portfolio value
        """
        holdings_value = sum(
            qty * prices.get(symbol, 0)
            for symbol, qty in self.holdings.items()
        )
        return self.cash + holdings_value

    def record_daily_snapshot(self, date: str, prices: Dict[str, float]):
        """
        Record a daily portfolio snapshot for tracking.

        Args:
            date: Date of snapshot (YYYY-MM-DD)
            prices: Current prices {symbol: price}
        """
        total_value = self.get_portfolio_value(prices)
        snapshot = {
            "date": date,
            "cash": self.cash,
            "holdings": dict(self.holdings),
            "holdings_value": total_value - self.cash,
            "total_value": total_value
        }
        self.portfolio_history.append(snapshot)

    def get_holdings_data(self, prices: Dict[str, float]) -> Dict[str, dict]:
        """
        Get current holdings with values (for AI prompt).

        Args:
            prices: Current prices {symbol: price}

        Returns:
            Holdings data formatted for AI
        """
        result = {}
        for symbol, quantity in self.holdings.items():
            price = prices.get(symbol, 0)
            avg_cost = self.average_cost.get(symbol, 0)
            result[symbol] = {
                "quantity": quantity,
                "average_buy_price": avg_cost,
                "current_price": price,
                "current_value": quantity * price,
                "profit_loss": (price - avg_cost) * quantity if avg_cost > 0 else 0
            }
        return result

    def get_summary(self, prices: Dict[str, float]) -> dict:
        """
        Get portfolio summary.

        Args:
            prices: Current prices {symbol: price}

        Returns:
            Summary dictionary
        """
        total_value = self.get_portfolio_value(prices)
        return {
            "starting_cash": self.starting_cash,
            "current_cash": self.cash,
            "holdings_count": len(self.holdings),
            "holdings_value": total_value - self.cash,
            "total_value": total_value,
            "total_return": (total_value - self.starting_cash) / self.starting_cash,
            "total_transactions": len(self.transactions)
        }
