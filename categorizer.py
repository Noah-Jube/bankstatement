"""Category rule engine: substring-keyword matching against transaction
descriptions, with rules persisted to a local JSON file."""
import json
import os
from typing import List

DEFAULT_RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules.json")

DEFAULT_RULES = {
    "default_category": "Uncategorized",
    "default_type": "Variable",
    "categories": {
        "Salary & Income": {"type": "Income", "keywords": [
            "SALARY", "SALARY PROCEEDS", "INTERBANK TRANSFER", "CREDIT INTEREST"
        ]},
        "Internal / Person-to-Person Transfers": {"type": "Transfer", "keywords": [
            "JUICE ACCOUNT TRANSFER", "JUICE TRANSFER", "INSTANT PAYMENT", "MERCHANT INSTANT PAYMENT"
        ]},
        "ATM Withdrawal": {"type": "Variable", "keywords": ["ATM CASH WITHDRAWAL", "ATM WITHDRAWAL"]},
        "Groceries": {"type": "Variable", "keywords": [
            "SUPERMARKET", "INTERMART", "WINNER'S", "WINNERS", "DREAM PRICE"
        ]},
        "Dining & Takeaway": {"type": "Variable", "keywords": [
            "KENTUCKY FRIED CHICKEN", "KFC", "PIZZA", "RESTAURANT", "SEVEN SEVEN",
            "SNACK", "CATERING", "WOK INN"
        ]},
        "Coffee & Cafes": {"type": "Variable", "keywords": ["ARTISAN COFFEE", "COFFEE"]},
        "Fuel & Transport": {"type": "Fixed", "keywords": [
            "OIL FILL", "INDIAN OIL", "IND OIL", "FUEL", "PETROL", "AUTOPARTS"
        ]},
        "Online Shopping": {"type": "Variable", "keywords": [
            "ALIEXPRESS", "TEMU", "AMAZON", "COURTS", "FASHION HOUSE", "DECATHLON"
        ]},
        "Subscriptions & Entertainment": {"type": "Fixed", "keywords": [
            "DISNEY PLUS", "NETFLIX", "SPOTIFY", "CANVA"
        ]},
        "Insurance": {"type": "Fixed", "keywords": ["INSURANCE"]},
        "Health & Pharmacy": {"type": "Fixed", "keywords": ["PHARMACY", "PHARMACIE", "MED LAB", "SANTE"]},
        "Bank Fees & Charges": {"type": "Fixed", "keywords": [
            "CHARGE", "VAT ON REFILL", "REFILL AMOUNT"
        ]},
        "Standing Orders": {"type": "Fixed", "keywords": ["STANDING ORDER"]},
        "Government & Tax": {"type": "Fixed", "keywords": ["REVENUE AUTHORITY", "MRA"]},
    },
}


def load_rules(path: str = DEFAULT_RULES_PATH) -> dict:
    if not os.path.exists(path):
        save_rules(DEFAULT_RULES, path)
        return json.loads(json.dumps(DEFAULT_RULES))
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_rules(rules: dict, path: str = DEFAULT_RULES_PATH) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rules, f, indent=2, ensure_ascii=False)


def add_rule(category: str, keywords: List[str], expense_type: str, path: str = DEFAULT_RULES_PATH) -> dict:
    rules = load_rules(path)
    cat = rules["categories"].setdefault(category, {"type": expense_type, "keywords": []})
    cat["type"] = expense_type
    existing = {k.upper() for k in cat["keywords"]}
    for kw in keywords:
        kw = kw.strip()
        if kw and kw.upper() not in existing:
            cat["keywords"].append(kw)
            existing.add(kw.upper())
    save_rules(rules, path)
    return rules


def delete_category(category: str, path: str = DEFAULT_RULES_PATH) -> dict:
    rules = load_rules(path)
    rules["categories"].pop(category, None)
    save_rules(rules, path)
    return rules


def categorize(description: str, rules: dict):
    desc_upper = description.upper()
    for category, meta in rules.get("categories", {}).items():
        for kw in meta.get("keywords", []):
            if kw.upper() in desc_upper:
                return category, meta.get("type", rules.get("default_type", "Variable"))
    return rules.get("default_category", "Uncategorized"), rules.get("default_type", "Variable")


def categorize_dataframe(df, rules: dict):
    cats, types = [], []
    for desc in df["description"]:
        c, t = categorize(desc, rules)
        cats.append(c)
        types.append(t)
    df = df.copy()
    df["category"] = cats
    df["expense_type"] = types
    return df
