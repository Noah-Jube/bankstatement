"""Streamlit entrypoint: upload -> parse -> categorize -> dashboard.

Run with: streamlit run app.py
"""
import streamlit as st

from core.pdf_parser import parse_pdf
from core.categorizer import categorize_transactions
from core.analytics import (
    transactions_to_dataframe,
    summary_metrics,
    category_breakdown,
    filter_transactions,
)

st.set_page_config(page_title="Bank Statement Analyzer", page_icon="💳", layout="wide")

st.title("💳 Bank Statement Analyzer")
st.caption("Upload a PDF bank statement — parsing and analysis all run locally.")

if "df" not in st.session_state:
    st.session_state.df = None
    st.session_state.filename = None

uploaded = st.file_uploader("Drop your PDF bank statement here", type=["pdf"])

if uploaded is not None and uploaded.name != st.session_state.filename:
    with st.spinner("Parsing statement..."):
        transactions = parse_pdf(uploaded.read())
        transactions = categorize_transactions(transactions)
        st.session_state.df = transactions_to_dataframe(transactions)
        st.session_state.filename = uploaded.name

df = st.session_state.df

if df is None:
    st.info("Upload a statement to get started.")
    st.stop()

if df.empty:
    st.warning(
        "No transactions could be parsed from this PDF. The statement's layout may not be "
        "supported yet — see the README for how to tune `core/pdf_parser.py` to your bank's format."
    )
    st.stop()

# ---- Summary metrics -----------------------------------------------------
metrics = summary_metrics(df)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Income", f"{metrics['total_income']:,.2f}")
c2.metric("Total Expenses", f"{metrics['total_expenses']:,.2f}")
c3.metric("Net", f"{metrics['net']:,.2f}")
c4.metric("Transactions", metrics["transaction_count"])

st.divider()

# ---- Category breakdown ---------------------------------------------------
st.subheader("Expenses by Category")
breakdown = category_breakdown(df)
if breakdown.empty:
    st.write("No expenses found.")
else:
    col1, col2 = st.columns([2, 1])
    with col1:
        st.bar_chart(breakdown.set_index("category")["total"])
    with col2:
        st.dataframe(breakdown, hide_index=True, use_container_width=True)

st.divider()

# ---- Transaction explorer -------------------------------------------------
st.subheader("All Transactions")

f1, f2, f3 = st.columns([2, 2, 1])
with f1:
    selected_categories = st.multiselect("Filter by category", sorted(df["category"].unique()))
with f2:
    search_text = st.text_input("Search description")
with f3:
    txn_type = st.selectbox("Type", ["All", "Credit", "Debit"])

filtered = filter_transactions(df, selected_categories, search_text, txn_type)

st.dataframe(
    filtered.rename(
        columns={
            "date": "Date",
            "description": "Description",
            "amount": "Amount",
            "type": "Type",
            "category": "Category",
        }
    ),
    hide_index=True,
    use_container_width=True,
)

st.download_button(
    "Download filtered transactions as CSV",
    filtered.to_csv(index=False).encode("utf-8"),
    file_name="transactions.csv",
    mime="text/csv",
)
