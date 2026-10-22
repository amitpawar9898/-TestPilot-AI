"""Class-based sample with planted bugs."""


class Cart:
    """A shopping cart."""

    def __init__(self, tax_rate=0.1):
        self.items = []
        self.tax_rate = tax_rate

    def add(self, price: float, qty: int = 1) -> int:
        """Add qty items at price; return how many line items the cart has."""
        self.items.append(price * qty)
        return len(self.items)

    def total(self) -> float:
        """Sum of all lines including tax."""
        return sum(self.items) * (1 + self.tax_rate)

    def average_item(self) -> float:
        """Average line value."""
        return sum(self.items) / len(self.items)  # BUG: empty cart

    def discounted(self, percent: float) -> float:
        """Total after a percentage discount."""
        return self.total() - self.total() * percent / 10  # BUG: should be / 100
