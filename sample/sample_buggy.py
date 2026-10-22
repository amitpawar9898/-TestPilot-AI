"""Sample module with deliberately planted bugs, used to demo TestPilot AI."""


def average(numbers):
    """Return the arithmetic mean of a list of numbers.

    >>> average([2, 4, 6])
    4.0
    """
    return sum(numbers) / len(numbers)  # BUG: ZeroDivisionError on an empty list


def apply_discount(price: float, percent: float) -> float:
    """Apply a percentage discount to a price.

    >>> apply_discount(100.0, 20)
    80.0
    """
    return price - price * percent / 10  # BUG: should divide by 100


def is_palindrome(text: str) -> bool:
    """True if text reads the same backwards, ignoring case and punctuation."""
    cleaned = [c.lower() for c in text if c.isalnum()]
    return cleaned == cleaned[::-1]


def clamp(value, low, high):
    """Limit value to the range [low, high]."""
    if value < low:
        return low
    if value > high:
        return high
    return value


def collect(item, bucket=[]):  # BUG: mutable default argument
    """Append item to bucket and return it."""
    bucket.append(item)
    return bucket


def fizzbuzz(n: int) -> str:
    """Classic FizzBuzz for a single number."""
    if n % 15 == 0:
        return "FizzBuzz"
    if n % 3 == 0:
        return "Fizz"
    if n % 5 == 0:
        return "Buzz"
    return str(n)
