"""PDF bank statement parsing utilities.

Extraction strategy, applied per page:
1. Try to pull structured tables (pdfplumber's table detector). This works
   well for statements that render transactions as a real table, which is
   most banks' "digital" PDF exports.
2. Fall back to line-by-line regex parsing of the raw text for statements
   that are just formatted text (fixed-width columns, no table grid).

Bank statement layouts vary a lot, so this parser is intentionally generic
and heuristic. Treat `_parse_table()` and `parse_line()` as the place to add
bank-specific tweaks -- see the README for a worked example.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pdfplumber

# --------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------

# (regex, strptime format) pairs, checked in order.
DATE_PATTERNS: list[tuple[str, str]] = [
    (r"\d{2}/\d{2}/\d{4}", "%d/%m/%Y"),
    (r"\d{2}-\d{2}-\d{4}", "%d-%m-%Y"),
    (r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"),
    (r"\d{2}/\d{2}/\d{2}\b", "%d/%m/%y"),
    (r"\d{2} [A-Za-z]{3} \d{4}", "%d %b %Y"),
    (r"[A-Za-z]{3} \d{1,2},? \d{4}", "%b %d, %Y"),
    (r"\d{2}\.\d{2}\.\d{4}", "%d.%m.%Y"),
]

AMOUNT_RE = re.compile(
    r"\(?-?\$?\s?\d+(?:,\d{3})*(?:\.\d{1,2})?\)?\s?(?:CR|DR)?", re.IGNORECASE
)

CREDIT_KEYWORDS = (
    "salary", "payroll", "deposit", "refund", "interest credit",
    "credit interest", "reversal", "cashback", "transfer in", "incoming",
)


@dataclass
class Transaction:
    date: datetime
    description: str
    amount: float           # always stored positive
    type: str                # "credit" | "debit"
    category: str = "Uncategorized"
    raw_line: str = field(default="", repr=False)

    def to_dict(self) -> dict:
        return {
            "date": self.date.date().isoformat(),
            "description": self.description,
            "amount": self.amount,
            "type": self.type,
            "category": self.category,
        }


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _find_date(text: str) -> Optional[tuple[datetime, str]]:
    for pattern, fmt in DATE_PATTERNS:
        m = re.search(pattern, text)
        if m:
            try:
                return datetime.strptime(m.group(0), fmt), m.group(0)
            except ValueError:
                continue
    return None


def _clean_amount(raw: str) -> tuple[float, bool]:
    """Parses a raw amount string. Returns (value, looks_like_a_debit)."""
    is_negative = "(" in raw or raw.strip().startswith("-")
    is_dr = bool(re.search(r"\bdr\b", raw, re.IGNORECASE))
    cleaned = re.sub(r"[^\d.]", "", raw)
    if not cleaned:
        return 0.0, False
    try:
        value = float(cleaned)
    except ValueError:
        return 0.0, False
    return value, (is_negative or is_dr)


def _find_amounts(text: str) -> list[str]:
    return [m.group(0).strip() for m in AMOUNT_RE.finditer(text) if m.group(0).strip()]


def _infer_type(description: str, negative: bool, line: str) -> str:
    """Line-based parsing has no debit/credit column to key off, so this is
    a heuristic: explicit negative/DR markers or "(...)" parens => debit;
    explicit CR marker or a known credit keyword (salary, refund, deposit,
    ...) => credit; otherwise default to debit, since the majority of lines
    on a statement are expenses and credits are the exception. Tune
    CREDIT_KEYWORDS above if your bank's statement disagrees.
    """
    if negative:
        return "debit"
    lowered = description.lower()
    if any(k in lowered for k in CREDIT_KEYWORDS):
        return "credit"
    if re.search(r"\bcr\b", line, re.IGNORECASE):
        return "credit"
    return "debit"


# --------------------------------------------------------------------------
# Line-based (unstructured text) parsing
# --------------------------------------------------------------------------

def parse_line(line: str) -> Optional[Transaction]:
    """Best-effort parse of a single text line into a Transaction.

    Assumes a layout roughly like:
        <date>  <description>  <amount>  [<running balance>]
    """
    line = line.strip()
    if not line:
        return None

    date_match = _find_date(line)
    if not date_match:
        return None
    date_val, date_str = date_match

    remainder = line[line.index(date_str) + len(date_str):]
    amounts = _find_amounts(remainder)
    if not amounts:
        return None

    # Heuristic: with 2+ numbers on the line, the LAST is usually the
    # running balance and the one before it is the transaction amount.
    amount_str = amounts[-2] if len(amounts) >= 2 else amounts[0]

    value, negative = _clean_amount(amount_str)
    if value == 0.0:
        return None

    description = remainder[: remainder.index(amount_str)].strip(" -|\t")
    description = re.sub(r"\s{2,}", " ", description) or "Unknown"

    txn_type = _infer_type(description, negative, line)

    return Transaction(
        date=date_val,
        description=description,
        amount=value,
        type=txn_type,
        raw_line=line,
    )


# --------------------------------------------------------------------------
# Table-based (structured) parsing
# --------------------------------------------------------------------------

def _parse_table(table: list[list[Optional[str]]]) -> list[Transaction]:
    """Interprets an extracted table as a transaction table, if recognizable.

    Handles both split debit/credit columns and a single signed amount
    column, keyed off the header row.
    """
    results: list[Transaction] = []
    if not table or len(table) < 2:
        return results

    header = [(h or "").lower().strip() for h in table[0]]

    def col_index(*names: str) -> Optional[int]:
        for i, h in enumerate(header):
            if any(n in h for n in names):
                return i
        return None

    date_idx = col_index("date")
    desc_idx = col_index("description", "details", "narration", "particulars")
    debit_idx = col_index("debit", "withdrawal")
    credit_idx = col_index("credit", "deposit")
    amount_idx = col_index("amount")

    if date_idx is None or desc_idx is None:
        return results  # not a recognizable transaction table

    for row in table[1:]:
        if not row or len(row) <= max(date_idx, desc_idx):
            continue

        raw_date = (row[date_idx] or "").strip()
        date_match = _find_date(raw_date)
        if not date_match:
            continue
        date_val, _ = date_match

        description = (row[desc_idx] or "").strip() or "Unknown"

        amount: Optional[float] = None
        txn_type: Optional[str] = None

        if debit_idx is not None and row[debit_idx] and row[debit_idx].strip():
            amount, _ = _clean_amount(row[debit_idx])
            txn_type = "debit"
        elif credit_idx is not None and row[credit_idx] and row[credit_idx].strip():
            amount, _ = _clean_amount(row[credit_idx])
            txn_type = "credit"
        elif amount_idx is not None and row[amount_idx]:
            amount, negative = _clean_amount(row[amount_idx])
            txn_type = "debit" if negative else "credit"

        if amount:
            results.append(
                Transaction(date=date_val, description=description, amount=amount, type=txn_type)
            )

    return results


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def parse_pdf(file_bytes: bytes) -> list[Transaction]:
    """Extracts transactions from a PDF's raw bytes."""
    transactions: list[Transaction] = []

    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            page_txns: list[Transaction] = []

            for table in page.extract_tables():
                page_txns.extend(_parse_table(table))

            if page_txns:
                transactions.extend(page_txns)
                continue  # table parsing worked for this page; skip line parsing

            text = page.extract_text() or ""
            for line in text.split("\n"):
                txn = parse_line(line)
                if txn:
                    transactions.append(txn)

    return transactions
