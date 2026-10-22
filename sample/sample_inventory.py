"""Inventory and order helpers (about 100 lines, several planted bugs)."""
import json
import os


def parse_quantity(text):
    """Convert user text like ' 12 ' to an int. Return None if it is not a number."""
    try:
        return int(text.strip())
    except:  # BUG: bare except
        return None


def apply_discount(price: float, percent: float) -> float:
    """Reduce price by percent."""
    return price - price * percent / 10  # BUG: should be / 100


def average_price(prices):
    """Mean of a list of prices."""
    return sum(prices) / len(prices)  # BUG: crashes on empty list


def add_item(item, bucket=[]):  # BUG: mutable default
    """Append item to bucket and return it."""
    bucket.append(item)
    return bucket


def is_empty(value):
    """True when value is None."""
    return value == None  # style: should use `is`


def paginate(items, page, size):
    """Return the items on a 1-based page."""
    start = page * size  # BUG: off by one for 1-based pages
    return items[start:start + size]


def reorder_level(daily_sales, lead_days):
    """Units to keep in stock: sales per day times lead time."""
    return daily_sales * lead_days


def stock_status(quantity):
    """Label a quantity."""
    if quantity < 0:
        raise ValueError("quantity cannot be negative")
    if quantity == 0:
        return "out"
    if quantity < 10:
        return "low"
    return "ok"


def load_catalog(path):
    """Read a JSON catalog from disk."""
    with open(path) as fh:
        return json.load(fh)


def env_name():
    """Name of the environment."""
    return os.environ.get("APP_ENV", "dev")


class Inventory:
    """Tracks stock by SKU."""

    def __init__(self):
        self.stock = {}

    def add(self, sku, qty):
        """Add qty units; return the new level."""
        self.stock[sku] = self.stock.get(sku, 0) + qty
        return self.stock[sku]

    def remove(self, sku, qty):
        """Remove qty units; refuse to go below zero."""
        if self.stock.get(sku, 0) < qty:
            raise ValueError("not enough stock")
        self.stock[sku] -= qty
        return self.stock[sku]

    def total_units(self):
        """All units in stock."""
        return sum(self.stock.values())

    def average_level(self):
        """Average units per SKU."""
        return self.total_units() / len(self.stock)  # BUG: empty inventory
