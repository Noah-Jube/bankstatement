"""
PDF bank-statement parser.

Design notes:
- Processes the PDF page-by-page. Transaction tracking is reset at every
  page boundary, so recurring headers/footers (title, IBAN, account
  holder address, column headers) are discarded automatically without
  needing to enumerate every possible boilerplate string.
- A transaction "block" starts on any line beginning with two dates
  (Trans Date, Value Date). Any subsequent non-date line on the SAME
  page, before the next date-line, is treated as a continuation of that
  transaction's description (handles multi-line merchant details).
- Debit/Credit direction is derived from the balance delta between
  consecutive rows, cross-checked against the amount printed on the row
  itself. This sidesteps ambiguity from flattened DEBIT/CREDIT columns.
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd
import pdfplumber

DATE_RE = r"\d{2}/\d{2}/\d{4}"
LINE_START_RE = re.compile(rf"^({DATE_RE})\s+({DATE_RE})\s+(.*)$")
MONEY_RE = re.compile(r"-?\d{1,3}(?:,\d{3})*\.\d{2}")

OPENING_RE = re.compile(r"^Opening Balance\s+(-?\d{1,3}(?:,\d{3})*\.\d{2})\s*$", re.IGNORECASE)
CLOSING_RE = re.compile(r"^Closing Balance\s+(-?\d{1,3}(?:,\d{3})*\.\d{2})\s*$", re.IGNORECASE)

# Lightweight safety-net patterns (secondary defense; the page-boundary
# reset does most of the work). Extend via the "extra noise patterns"
# box in the UI if a specific bank template needs more.
NOISE_PATTERNS = [
    r"Regular Account STATEMENT",
    r"^Page\s*:\s*\d+\s*of\s*\d+",
    r"^Account Number",
    r"^Currency\s*$",
    r"^Statement Date",
    r"^Despatch Code",
    r"^IBAN\s*:",
    r"^From\s+\d{2}/\d{2}/\d{4}\s+to\s+\d{2}/\d{2}/\d{4}",
    r"^TRANS\s*$",
    r"^VALUE\s*$",
    r"^DATE\s*$",
    r"^TRANSACTION DETAILS",
    r"DEBIT\s+CREDIT\s+BALANCE",
    r"Indicates a debit",
    r"P\.O\.Box",
    r"Swift Code",
    r"Website\s*:",
    r"^In case you are not agreeable",
    r"^Please compare this statement",
    r"^Otherwise, you may visit",
    r"^Beware of phishing",
    r"^To ensure your security",
    r"^Do not access IB",
    r"even to your bank",
    r"^regular annual audit",
    r"^auditors will advise",
]
NOISE_RE = re.compile("|".join(NOISE_PATTERNS), re.IGNORECASE)

REFERENCE_RE = re.compile(r"FT[0-9A-Za-z]+(?:\\BNK)?")


@dataclass
class RawTxn:
    trans_date: str
    value_date: str
    lines: List[str] = field(default_factory=list)


def _extract_pages(file) -> List[List[str]]:
    pages_lines: List[List[str]] = []
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            pages_lines.append(lines)
    return pages_lines


def _split_amounts(remainder: str):
    """Return (description, amount_str_or_None, balance_str) from a
    transaction's first line, using the last two money-like tokens."""
    remainder = remainder.strip()
    matches = list(MONEY_RE.finditer(remainder))
    if not matches:
        return remainder, None, None
    balance_m = matches[-1]
    amount_m = matches[-2] if len(matches) >= 2 else None
    cut_at = amount_m.start() if amount_m else balance_m.start()
    desc = remainder[:cut_at].strip()
    amount = amount_m.group(0) if amount_m else None
    balance = balance_m.group(0)
    return desc, amount, balance


def _to_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    return float(value.replace(",", ""))


def parse_statement(file, extra_noise_patterns: Optional[List[str]] = None) -> pd.DataFrame:
    noise_re = NOISE_RE
    if extra_noise_patterns:
        cleaned = [p for p in extra_noise_patterns if p and p.strip()]
        if cleaned:
            noise_re = re.compile("|".join(NOISE_PATTERNS + cleaned), re.IGNORECASE)

    pages = _extract_pages(file)

    raw_txns: List[RawTxn] = []
    opening_balance: Optional[float] = None
    closing_balance: Optional[float] = None

    for page_lines in pages:
        current: Optional[RawTxn] = None  # reset at every page boundary
        for line in page_lines:
            m = LINE_START_RE.match(line)
            if m:
                trans_date, value_date, remainder = m.groups()
                current = RawTxn(trans_date=trans_date, value_date=value_date, lines=[remainder])
                raw_txns.append(current)
                continue

            om = OPENING_RE.match(line)
            if om:
                opening_balance = _to_float(om.group(1))
                current = None
                continue

            cm = CLOSING_RE.match(line)
            if cm:
                closing_balance = _to_float(cm.group(1))
                current = None
                continue

            if noise_re.search(line):
                current = None
                continue

            if current is not None:
                current.lines.append(line)
            # else: header line before this page's first transaction -> drop

    records = []
    running_balance = opening_balance
    for txn in raw_txns:
        desc_first, amount_str, balance_str = _split_amounts(txn.lines[0])
        full_desc = " ".join([desc_first] + txn.lines[1:]).strip()
        full_desc = re.sub(r"\s+", " ", full_desc)

        amount = _to_float(amount_str)
        balance = _to_float(balance_str)

        debit = credit = 0.0
        if balance is not None:
            if running_balance is not None:
                delta = round(balance - running_balance, 2)
                if amount is not None and abs(abs(delta) - amount) < 0.02:
                    if delta < 0:
                        debit = amount
                    else:
                        credit = amount
                else:
                    # Amount/delta disagree (rare formatting edge case) -> trust the balance delta
                    if delta < 0:
                        debit = abs(delta)
                    else:
                        credit = delta
            elif amount is not None:
                debit = amount  # no opening balance available to infer direction
            running_balance = balance
        elif amount is not None:
            debit = amount

        ref_match = REFERENCE_RE.search(full_desc)
        reference = ref_match.group(0) if ref_match else ""
        clean_desc = re.sub(r"\s+", " ", REFERENCE_RE.sub("", full_desc)).strip()

        records.append({
            "trans_date": pd.to_datetime(txn.trans_date, format="%d/%m/%Y"),
            "value_date": pd.to_datetime(txn.value_date, format="%d/%m/%Y"),
            "description": clean_desc,
            "reference": reference,
            "debit": round(debit, 2),
            "credit": round(credit, 2),
            "amount": round(credit - debit, 2),
            "balance": balance,
        })

    df = pd.DataFrame.from_records(records)
    if df.empty:
        df.attrs["opening_balance"] = opening_balance
        df.attrs["closing_balance"] = closing_balance
        return df

    df["flow"] = df["debit"].apply(lambda x: "Debit" if x > 0 else "Credit")
    df.sort_values("trans_date", kind="stable", inplace=True)
    df.reset_index(drop=True, inplace=True)
    df.attrs["opening_balance"] = opening_balance
    df.attrs["closing_balance"] = closing_balance
    return df
