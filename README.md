# Invoice Desk

A vendor invoice PDF goes in. A reasoned decision comes out, with every stage visible as it runs.

**The model reads; code decides.** The reader (Gemini, or a free local reader) only copies what is printed on the invoice. Plain Python rules in `core/engine.py` make every decision, so the same invoice always gets the same answer, and an invoice cannot talk the system into approving it.

## The process

| # | Stage | What runs | AI? |
|---|---|---|---|
| 1 | Receive and read | Fingerprint (SHA-256), PDF / size / page checks, each page classed as digital or scanned | No |
| 2 | Extract | Gemini sees page images only and must answer "not on document" rather than guess. Without a key, or if Gemini fails, the local reader uses the text layer, or two OCR passes for scans | Transcription only |
| 3 | Validate | Required fields, vendor in master, GSTIN checksum, billed to us, line arithmetic, footing, GST rate and tax type | No |
| 4 | Duplicate check | Same file, same IRN, same number in the year (reject); near-identical number or same amount close in time (hold) | No |
| 5 | PO match | Price and quantity against the PO and goods receipt, cumulative across every earlier invoice on that PO | No |
| 6 | Security | Tax ID and bank details against the vendor master. Always runs, even after a perfect match | No |
| 7 | Decide | Worst reason wins. Status, owner, one specific question, payable and disputed amounts | No |

## Demo invoices (run in this order after **Reset demo data**)

| Run | File | Expected result |
|---|---|---|
| 1 | `1_happy_path_NIM-0417.pdf` | Approved, ₹18,29,000 |
| 2 | `2_duplicate_scan_NIM-417-R.pdf` | Possible duplicate of Run 1 (scan, `-R` suffix). PO match skipped |
| 3 | `3_cumulative_overrun_KSS-0124.pdf` | Doesn't match the PO: ₹2,83,200 payable, ₹24,780 disputed |
| 4 | `4_bank_change_SIS-2291.pdf` | Stopped for a security check. PO match passes, bank details differ |
| 5 | `5_control_close_but_ok_SIS-2307.pdf` | Approved with a note: ₹1,600 over PO, inside the ₹4,700 limit |

Check them all without the UI: `python tests/test_demo_runs.py`

## Run it on your laptop

```bash
pip install -r requirements.txt
# also install Tesseract OCR: Windows installer from UB Mannheim, macOS `brew install tesseract`, Linux `apt install tesseract-ocr`
streamlit run app.py
```

## Put it online for free (Streamlit Community Cloud)

1. Create a new **public** GitHub repository and upload every file in this folder (keep the folder structure, including `.streamlit/config.toml`, `packages.txt` and `samples/`).
2. Go to share.streamlit.io, sign in with GitHub, choose **Create app**, pick the repository, branch `main`, main file `app.py`, then **Deploy**.
3. Optional: in the app's **Settings → Secrets**, paste the lines from `.streamlit/secrets.toml.example` with your own values.

`packages.txt` installs Tesseract on the server for scanned invoices. The free tier sleeps after a period without visitors; open the link a few minutes before the interview.

## Files

- `app.py` – the four screens: New invoice (live run), Case file, Dashboard, Policy
- `core/engine.py` – the decision rules, a line-for-line port of `decisionEngine.ts`
- `core/reader.py` – receive, render, OCR, the local reader, the Gemini reader and per-field evidence
- `core/pipeline.py` – the seven stages in order, each writing real events
- `core/db.py` – SQLite: master data, ledger, runs, the event log, reviews
- `core/ui.py` – HTML building blocks for the screens
