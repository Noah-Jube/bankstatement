import io

import altair as alt
import pandas as pd
import streamlit as st

from parser import parse_statement
from categorizer import load_rules, add_rule, delete_category, categorize_dataframe

st.set_page_config(page_title="Bank Statement Analyzer", layout="wide")


@st.cache_data(show_spinner=False)
def _parse_pdf_bytes(file_bytes: bytes, extra_patterns: tuple) -> pd.DataFrame:
    return parse_statement(io.BytesIO(file_bytes), extra_noise_patterns=list(extra_patterns))


# ---------------- Sidebar: Upload ----------------
st.sidebar.title("📄 Statement")
uploaded = st.sidebar.file_uploader("Upload a PDF bank statement", type=["pdf"])

with st.sidebar.expander("⚙️ Advanced parsing options"):
    extra_text = st.text_area(
        "Extra noise patterns (regex, one per line)",
        help="If continuation lines look corrupted with letterhead text "
             "(your name, address, etc.), add distinctive fragments here "
             "to have them ignored.",
        height=80,
    )
extra_patterns = tuple(l.strip() for l in extra_text.splitlines() if l.strip()) if extra_text else tuple()

if uploaded is None:
    st.title("🏦 Bank Statement Analyzer")
    st.info("Upload a PDF bank statement from the sidebar to get started.")
    st.stop()

try:
    with st.spinner("Parsing PDF..."):
        raw_df = _parse_pdf_bytes(uploaded.getvalue(), extra_patterns)
except Exception as e:
    st.error(f"Failed to parse PDF: {e}")
    st.stop()

if raw_df.empty:
    st.error("No transactions could be parsed from this PDF. The layout may not match the expected format.")
    st.stop()

# Track which file is loaded so a fresh upload starts with a clean slate
# of manually-deleted rows (row_id is just the stable positional index
# assigned during parsing, so it's consistent across reruns of the same file).
file_key = f"{uploaded.name}_{uploaded.size}"
if st.session_state.get("current_file_key") != file_key:
    st.session_state.current_file_key = file_key
    st.session_state.deleted_row_ids = set()
st.session_state.setdefault("deleted_row_ids", set())

rules = load_rules()
df = categorize_dataframe(raw_df, rules)
df["flow_amount"] = df[["debit", "credit"]].max(axis=1)

# Rows removed via the "Remove Transactions" control in the Transactions
# tab are excluded here, upstream of the filter sidebar and every tab, so
# KPIs, charts, and category breakdowns all reflect the edited dataset.
df = df[~df.index.isin(st.session_state.deleted_row_ids)].copy()

opening_balance = raw_df.attrs.get("opening_balance")
closing_balance = raw_df.attrs.get("closing_balance")

st.title("🏦 Bank Statement Analyzer")

# ---------------- Reconciliation check ----------------
# Deliberately checked against raw_df (the untouched parse output), not df,
# so that rows you delete in the Transactions tab don't make a correctly
# parsed statement look broken here.
if opening_balance is not None and closing_balance is not None:
    computed_closing = opening_balance + raw_df["credit"].sum() - raw_df["debit"].sum()
    if abs(computed_closing - closing_balance) < 0.02:
        st.success(
            f"✅ Parsed {len(raw_df)} transactions — balances reconcile "
            f"(Opening MUR {opening_balance:,.2f} → Closing MUR {closing_balance:,.2f})."
        )
    else:
        st.warning(
            f"⚠️ Parsed {len(raw_df)} transactions, but computed closing balance "
            f"(MUR {computed_closing:,.2f}) doesn't match the statement's stated "
            f"closing balance (MUR {closing_balance:,.2f}). Check the Debug tab."
        )

if st.session_state.deleted_row_ids:
    st.caption(f"🗑️ {len(st.session_state.deleted_row_ids)} transaction(s) removed this session "
               f"— restore them from the Transactions tab.")

# ---------------- Sidebar: Filters ----------------
st.sidebar.title("🔎 Filters")

min_date, max_date = df["trans_date"].min().date(), df["trans_date"].max().date()
date_range = st.sidebar.date_input("Date range", value=(min_date, max_date),
                                    min_value=min_date, max_value=max_date)
start_date, end_date = date_range if isinstance(date_range, tuple) and len(date_range) == 2 else (min_date, max_date)

flow_choice = st.sidebar.radio("Transaction flow", ["All", "Expenses / Debits only", "Income / Credits only"])

max_amt = float(df["flow_amount"].max()) or 1.0
amt_range = st.sidebar.slider("Amount range (MUR)", min_value=0.0, max_value=round(max_amt, 2),
                               value=(0.0, round(max_amt, 2)))

all_categories = sorted(df["category"].unique().tolist())
selected_categories = st.sidebar.multiselect("Categories", all_categories, default=all_categories)

search_text = st.sidebar.text_input("Search description")

high_value_threshold = st.sidebar.number_input("🚩 High-value flag threshold (MUR)",
                                                 min_value=0.0, value=1000.0, step=100.0)

# ---------------- Apply filters ----------------
mask = (df["trans_date"].dt.date >= start_date) & (df["trans_date"].dt.date <= end_date)
if flow_choice == "Expenses / Debits only":
    mask &= df["debit"] > 0
elif flow_choice == "Income / Credits only":
    mask &= df["credit"] > 0
mask &= df["flow_amount"].between(amt_range[0], amt_range[1])
if selected_categories:
    mask &= df["category"].isin(selected_categories)
if search_text:
    mask &= df["description"].str.contains(search_text, case=False, na=False)

filtered = df[mask].copy()

# ---------------- KPIs ----------------
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Debits", f"MUR {filtered['debit'].sum():,.2f}")
c2.metric("Total Credits", f"MUR {filtered['credit'].sum():,.2f}")
c3.metric("Net Change", f"MUR {(filtered['credit'].sum() - filtered['debit'].sum()):,.2f}")
c4.metric("Transactions", f"{len(filtered)}")

st.divider()

tab_txns, tab_analytics, tab_trends, tab_flags, tab_rules, tab_debug = st.tabs(
    ["📋 Transactions", "📊 Category Breakdown", "📈 Monthly Trends", "🚩 Flagged", "⚙️ Category Rules", "🐛 Debug"]
)

# ---------------- Transactions ----------------
with tab_txns:
    filtered_sorted = filtered.sort_values("trans_date")

    # --- Row deletion control -------------------------------------------------
    with st.expander(f"🗑️ Remove transactions from this session ({len(filtered_sorted)} shown)"):
        st.caption("Removed rows drop out of every table, chart, and total on this "
                   "page (parsing/reconciliation checks above are unaffected). "
                   "Removal only affects this browser session — the source PDF is never changed.")

        def _row_label(idx):
            r = filtered_sorted.loc[idx]
            amt = f"-{r['debit']:,.2f}" if r["debit"] > 0 else f"+{r['credit']:,.2f}"
            desc = r["description"][:60]
            return f"{r['trans_date'].strftime('%d/%m/%Y')}  •  {desc}  •  MUR {amt}"

        rows_to_delete = st.multiselect(
            "Select transactions to remove",
            options=filtered_sorted.index.tolist(),
            format_func=_row_label,
            key="rows_to_delete_select",
        )

        col_del, col_restore = st.columns(2)
        with col_del:
            if st.button(f"Delete {len(rows_to_delete)} selected row(s)", disabled=not rows_to_delete):
                st.session_state.deleted_row_ids.update(rows_to_delete)
                st.rerun()
        with col_restore:
            if st.button("Restore all removed rows", disabled=not st.session_state.deleted_row_ids):
                st.session_state.deleted_row_ids = set()
                st.rerun()

    # --- Table with a styled Total row -----------------------------------------
    display_df = filtered_sorted[["trans_date", "value_date", "description", "category", "expense_type",
                                   "debit", "credit", "balance"]].copy()
    display_df["trans_date"] = display_df["trans_date"].dt.strftime("%d/%m/%Y")
    display_df["value_date"] = display_df["value_date"].dt.strftime("%d/%m/%Y")
    display_df = display_df.rename(columns={
        "trans_date": "Trans Date", "value_date": "Value Date", "description": "Description",
        "category": "Category", "expense_type": "Type", "debit": "Debit", "credit": "Credit", "balance": "Balance",
    })

    total_row = pd.DataFrame([{
        "Trans Date": "", "Value Date": "", "Description": "TOTAL",
        "Category": "", "Type": "",
        "Debit": display_df["Debit"].sum(),
        "Credit": display_df["Credit"].sum(),
        "Balance": None,
    }])
    display_with_total = pd.concat([display_df, total_row], ignore_index=True)

    def _highlight_total(row):
        is_total = row["Description"] == "TOTAL"
        style = "font-weight: bold; background-color: #FFE58F; border-top: 2px solid #333;" if is_total else ""
        return [style] * len(row)

    styled = (
        display_with_total.style
        .apply(_highlight_total, axis=1)
        .format({"Debit": "{:,.2f}", "Credit": "{:,.2f}", "Balance": "{:,.2f}"}, na_rep="")
    )
    st.dataframe(styled, use_container_width=True, hide_index=True)

    csv_bytes = display_df.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Export filtered transactions to CSV (excludes Total row)", data=csv_bytes,
                        file_name="filtered_transactions.csv", mime="text/csv")

# ---------------- Category Breakdown ----------------
with tab_analytics:
    spend_df = filtered[filtered["debit"] > 0]
    if spend_df.empty:
        st.info("No expense transactions in the current filter selection.")
    else:
        by_cat = spend_df.groupby("category")["debit"].sum().reset_index().sort_values("debit", ascending=False)
        col1, col2 = st.columns(2)
        with col1:
            st.altair_chart(
                alt.Chart(by_cat).mark_bar().encode(
                    x=alt.X("debit:Q", title="Total Spent (MUR)"),
                    y=alt.Y("category:N", sort="-x", title="Category"),
                    tooltip=["category", "debit"],
                ).properties(height=420, title="Spending by Category"),
                use_container_width=True,
            )
        with col2:
            st.altair_chart(
                alt.Chart(by_cat).mark_arc(innerRadius=60).encode(
                    theta="debit:Q", color="category:N", tooltip=["category", "debit"]
                ).properties(height=420, title="Spending Share"),
                use_container_width=True,
            )

        by_type = spend_df.groupby("expense_type")["debit"].sum().reset_index()
        st.altair_chart(
            alt.Chart(by_type).mark_arc(innerRadius=60).encode(
                theta="debit:Q", color="expense_type:N", tooltip=["expense_type", "debit"]
            ).properties(height=350, title="Fixed vs. Variable Expenses"),
            use_container_width=True,
        )

# ---------------- Monthly Trends ----------------
with tab_trends:
    trend_df = filtered.copy()
    trend_df["month"] = trend_df["trans_date"].dt.to_period("M").astype(str)

    monthly = trend_df.groupby("month").agg(Debits=("debit", "sum"), Credits=("credit", "sum")).reset_index()
    monthly_melt = monthly.melt("month", var_name="Flow", value_name="Amount")
    st.altair_chart(
        alt.Chart(monthly_melt).mark_line(point=True).encode(
            x=alt.X("month:N", title="Month"), y="Amount:Q", color="Flow:N",
            tooltip=["month", "Flow", "Amount"],
        ).properties(height=400, title="Monthly Debits vs. Credits"),
        use_container_width=True,
    )

    monthly_cat = trend_df[trend_df["debit"] > 0].groupby(["month", "category"])["debit"].sum().reset_index()
    st.altair_chart(
        alt.Chart(monthly_cat).mark_bar().encode(
            x="month:N", y="debit:Q", color="category:N", tooltip=["month", "category", "debit"],
        ).properties(height=400, title="Monthly Spend by Category"),
        use_container_width=True,
    )

# ---------------- Flagged ----------------
with tab_flags:
    st.subheader("🚩 Potential Duplicate Charges (same date, amount & description)")
    dup_mask = filtered.duplicated(subset=["trans_date", "debit", "description"], keep=False) & (filtered["debit"] > 0)
    dup_df = filtered[dup_mask].sort_values(["trans_date", "description"])
    if dup_df.empty:
        st.success("No potential duplicate charges found.")
    else:
        st.dataframe(dup_df[["trans_date", "description", "debit"]], use_container_width=True, hide_index=True)

    st.subheader(f"🚩 High-Value Transactions (> MUR {high_value_threshold:,.2f})")
    high_df = filtered[filtered["debit"] > high_value_threshold].sort_values("debit", ascending=False)
    if high_df.empty:
        st.success("No transactions above the threshold.")
    else:
        st.dataframe(high_df[["trans_date", "description", "category", "debit"]],
                     use_container_width=True, hide_index=True)

# ---------------- Category Rules ----------------
with tab_rules:
    st.caption("Rules match as case-insensitive substrings against each transaction's "
               "description, and are saved to rules.json so they persist across restarts.")

    with st.form("add_rule_form", clear_on_submit=True):
        colA, colB, colC = st.columns([2, 3, 1])
        cat_name = colA.text_input("Category name")
        keywords_raw = colB.text_input("Keywords (comma-separated)")
        exp_type = colC.selectbox("Type", ["Fixed", "Variable", "Income", "Transfer"])
        if st.form_submit_button("Add / Update Rule"):
            if cat_name and keywords_raw:
                add_rule(cat_name, [k.strip() for k in keywords_raw.split(",") if k.strip()], exp_type)
                st.success(f"Saved rule for '{cat_name}'.")
                st.rerun()
            else:
                st.warning("Please provide both a category name and at least one keyword.")

    st.divider()
    st.write("### Existing Rules")
    for cat, meta in rules["categories"].items():
        with st.expander(f"{cat}  •  {meta.get('type')}"):
            st.write(", ".join(meta.get("keywords", [])))
            if st.button(f"Delete '{cat}'", key=f"del_{cat}"):
                delete_category(cat)
                st.rerun()

# ---------------- Debug ----------------
with tab_debug:
    st.write("Opening balance:", opening_balance)
    st.write("Closing balance (stated):", closing_balance)
    st.write("Closing balance (computed from raw parsed transactions):",
              round((opening_balance or 0) + raw_df["credit"].sum() - raw_df["debit"].sum(), 2))
    st.write("Rows removed this session:", len(st.session_state.deleted_row_ids))
    st.write("Net change (computed from active, edited transactions):",
              round(df["credit"].sum() - df["debit"].sum(), 2))
    st.write("Raw parsed (uncategorized) transactions:")
    st.dataframe(raw_df, use_container_width=True)
