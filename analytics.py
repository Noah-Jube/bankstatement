"""Rule-based expense categorization."""
from __future__ import annotations

from config.categories import CATEGORY_RULES, DEFAULT_CATEGORY


def categorize(description: str) -> str:
    """Returns the first matching category for a transaction description."""
    lowered = description.lower()
    for category, keywords in CATEGORY_RULES.items():
        if any(keyword in lowered for keyword in keywords):
            return category
    return DEFAULT_CATEGORY


def categorize_transactions(transactions: list) -> list:
    """Mutates each Transaction's `.category` in place and returns the list."""
    for txn in transactions:
        txn.category = categorize(txn.description)
    return transactions
