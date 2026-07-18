"""Aggregation and filtering helpers for parsed transactions."""
from __future__ import annotations

import pandas as pd


def transactions_to_dataframe(transactions: list) -> pd.DataFrame:
    if not transactions:
        return pd.DataFrame(columns=["date", "description", "amount", "type", "category"])
    df = pd.DataFrame([t.to_dict() for t in transactions])
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def summary_metrics(df: pd.DataFrame) -> dict:
    income = df.loc[df["type"] == "credit", "amount"].sum()
    expenses = df.loc[df["type"] == "debit", "amount"].sum()
    return {
        "total_income": round(float(income), 2),
        "total_expenses": round(float(expenses), 2),
        "net": round(float(income - expenses), 2),
        "transaction_count": int(len(df)),
    }


def category_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    """Debit-only breakdown, sorted by total spend descending."""
    expenses = df[df["type"] == "debit"]
    if expenses.empty:
        return pd.DataFrame(columns=["category", "total", "count"])
    grouped = (
        expenses.groupby("category")["amount"]
        .agg(total="sum", count="count")
        .reset_index()
        .sort_values("total", ascending=False)
    )
    grouped["total"] = grouped["total"].round(2)
    return grouped


def filter_transactions(
    df: pd.DataFrame,
    categories: list[str] | None = None,
    search: str = "",
    txn_type: str | None = None,
) -> pd.DataFrame:
    result = df.copy()
    if categories:
        result = result[result["category"].isin(categories)]
    if search:
        result = result[result["description"].str.contains(search, case=False, na=False)]
    if txn_type and txn_type != "All":
        result = result[result["type"] == txn_type.lower()]
    return result
