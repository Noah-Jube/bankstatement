# Bank Statement Analyzer

A local Streamlit app that parses PDF bank statements, categorizes
transactions via user-editable keyword rules, and gives you filterable
tables + charts.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Then open the local URL Streamlit prints (usually http://localhost:8501)
and upload a PDF statement from the sidebar.

## Files

- `app.py` — Streamlit UI: filters, KPIs, charts, category rule editor
- `parser.py` — PDF -> DataFrame extraction (balance-delta debit/credit logic)
- `categorizer.py` — keyword-to-category rule engine, persists to `rules.json`
- `rules.json` — auto-created on first run; editable from the UI's
  "⚙️ Category Rules" tab, or by hand
- `requirements.txt` — Python dependencies

## Notes

- Works on text-based PDFs (not scanned/image-only statements — those
  would need an OCR pre-pass, which isn't included here).
- The parser was built and tested against a standard "two date columns +
  running balance" statement layout. If you use it with a different
  bank's format and the reconciliation check in the "🐛 Debug" tab shows
  a mismatch, inspect the raw parsed rows there and add any stray
  letterhead text to "Extra noise patterns" in the sidebar.
