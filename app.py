"""Invoice Desk: vendor invoice PDF in, reasoned decision out, every stage visible.

Run locally:   pip install -r requirements.txt   then   streamlit run app.py
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from core import db, engine, pipeline, ui

st.set_page_config(page_title="Invoice Desk", page_icon=":material/receipt_long:", layout="wide")
st.markdown(ui.CSS, unsafe_allow_html=True)
db.init()

ROOT = os.path.dirname(os.path.abspath(__file__))
SAMPLES = [
    ("1_happy_path_NIM-0417.pdf", "Happy path", "Clean digital e-invoice. PO, receipt and price all agree."),
    ("2_duplicate_scan_NIM-417-R.pdf", "Duplicate sent as a scan", "Run 1 re-sent as a scanned image with a -R suffix. Run after Run 1."),
    ("3_cumulative_overrun_KSS-0124.pdf", "Split PO, cumulative overrun", "Looks fine alone. Two earlier bills on the same PO push it over the limit."),
    ("4_bank_change_SIS-2291.pdf", "Changed bank details", "Every match is green, but the bank account differs from the vendor master."),
    ("5_control_close_but_ok_SIS-2307.pdf", "Close but within tolerance", "Price ₹4 a unit above the PO, inside the limit. Should still be approved."),
]


def secret(name, default=None):
    try:
        return st.secrets.get(name, os.environ.get(name, default))
    except Exception:
        return os.environ.get(name, default)


GEMINI_KEY = secret("GEMINI_API_KEY")
GEMINI_MODEL = secret("GEMINI_MODEL", "gemini-flash-latest")
PASSCODE = secret("APP_PASSCODE")


def can_run() -> bool:
    if not PASSCODE or st.session_state.get("unlocked"):
        return True
    code = st.text_input("Passcode to run invoices", type="password", help="Shared in the submission email. Browsing past runs needs no passcode.")
    if code and code == PASSCODE:
        st.session_state["unlocked"] = True
        return True
    if code:
        st.error("That passcode is not right.")
    return False


def open_case(run_id):
    st.session_state["open_run"] = run_id
    st.switch_page(PAGES["case"])


def age(iso):
    if not iso:
        return ""
    mins = int((datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() // 60)
    return f"{mins} min" if mins < 60 else (f"{mins // 60} h" if mins < 1440 else f"{mins // 1440} d")


def reset_button(key):
    with st.popover("Reset demo data", help="Clears every run and returns the ledger to its seeded state"):
        st.write("This deletes all runs, reviews and processed invoices. Vendors, POs, receipts and the two earlier Kaveri bills stay.")
        if st.button("Yes, reset", type="primary", key=key):
            counts = db.reset_demo()
            st.session_state.pop("open_run", None)
            st.toast(f"Reset done: {counts['runs']} runs and {counts['ledger rows']} ledger rows removed.")
            st.rerun()


# ------------------------------------------------------------------ live processing
def process(pdf_bytes: bytes, file_name: str, is_sample: bool):
    if pipeline.runs_today() >= pipeline.DAILY_CAP:
        st.error(f"The demo's daily limit of {pipeline.DAILY_CAP} runs has been reached. Saved runs can still be opened.")
        return
    st.markdown('<div class="idk-eyebrow">Live run</div>', unsafe_allow_html=True)
    head_ph = st.empty()
    left, centre, right = st.columns([1.05, 1.6, 1.15], gap="large")
    with left:
        tl_ph = st.empty()
    with centre:
        detail_ph = st.empty()
    with right:
        img_ph = st.empty()
    shown = {"image": False}

    def on_event(run_id):
        run = db.get_run(run_id)
        events = db.get_events(run_id)
        head_ph.markdown(ui.header(run), unsafe_allow_html=True)
        tl_ph.markdown(ui.timeline(events, pipeline.STAGES), unsafe_allow_html=True)
        last = next((e for e in reversed(events) if e["phase"] == "finished"), None)
        running = next((e["stage"] for e in reversed(events) if e["phase"] == "started"), None)
        if last:
            html = (f'<div class="idk-eyebrow">{ui.escape(last["stage"])}</div>'
                    + ui.checks_table((last["payload"] or {}).get("checks", []), extract=last["stage"] == "Extract"))
            if running and running != last["stage"]:
                html = f'<div class="idk-sub">Now running: <b>{ui.escape(running)}</b></div>' + html
            detail_ph.markdown(html, unsafe_allow_html=True)
        elif running:
            detail_ph.markdown(f'<div class="idk-sub">Now running: <b>{ui.escape(running)}</b></div>', unsafe_allow_html=True)
        img = os.path.join(db.FILES_DIR, run_id, "page-1.png")
        if not shown["image"] and os.path.exists(img):
            img_ph.image(img, caption="Page 1, as the reader saw it")
            shown["image"] = True

    run_id = pipeline.run_invoice(pdf_bytes, file_name, is_sample, GEMINI_KEY, GEMINI_MODEL, on_event=on_event)
    open_case(run_id)


# ------------------------------------------------------------------ pages
def page_new():
    st.markdown('<div class="idk-eyebrow">Accounts payable · Vanashree Precision Components</div>', unsafe_allow_html=True)
    st.title("New invoice")
    reader_chip = (f'<span class="idk-chip teal">Reader: Gemini, images only</span><span class="idk-chip">Fallback: local text layer / OCR</span>'
                   if GEMINI_KEY else '<span class="idk-chip">Reader: local text layer / OCR (no AI key set)</span>')
    st.markdown(f'<div class="idk-sub">Drop a vendor invoice. The reader only copies what is printed; written rules make every decision, '
                f'and each stage below is recorded as it happens.</div>{reader_chip}', unsafe_allow_html=True)
    st.write("")
    if not can_run():
        st.info("Enter the passcode to process invoices. You can still open saved runs from the Dashboard.")
        return

    up = st.file_uploader("Invoice PDF", type=["pdf"], help="One PDF, up to 10 MB and 5 pages")
    if up is not None and st.button("Process this invoice", type="primary"):
        process(up.getvalue(), up.name, False)
        return

    st.subheader("Try a sample")
    st.caption("Run them in this order after a reset. Runs 2 and 3 depend on what came before.")
    for i, (fname, title, desc) in enumerate(SAMPLES, start=1):
        path = os.path.join(ROOT, "samples", fname)
        c1, c2, c3 = st.columns([5, 1, 1])
        c1.markdown(f'<div class="idk-sample"><div class="t">{i}. {title}</div><div class="d">{desc}</div></div>', unsafe_allow_html=True)
        if c2.button("Run", key=f"run-{i}"):
            with open(path, "rb") as fh:
                process(fh.read(), fname, True)
            return
        with open(path, "rb") as fh:
            c3.download_button("PDF", fh.read(), file_name=fname, mime="application/pdf", key=f"dl-{i}")

    st.subheader("Recent runs")
    recent = db.rows("select * from runs order by started_at desc limit 5")
    if not recent:
        st.markdown('<div class="idk-empty">No runs yet.</div>', unsafe_allow_html=True)
    for r in recent:
        c1, c2 = st.columns([6, 1])
        label = ui.status_chip(r["decision"], r["decision_label"] or r["status"].replace("_", " "), r["final_decision"])
        amt = engine.format_inr(r["gross_paise"]) if r["gross_paise"] is not None else ""
        c1.markdown(f'{label} <b>{ui.escape(r["vendor_name"] or r["file_name"] or "")}</b> · {ui.escape(r["invoice_number"] or "")} · {amt}',
                    unsafe_allow_html=True)
        if c2.button("Open", key=f"open-{r['id']}"):
            open_case(r["id"])
    st.write("")
    reset_button("reset-new")


def review_card(run):
    st.markdown(f'<div class="idk-card"><div class="idk-eyebrow">Needs a person: {ui.escape(run["owner"] or "")}</div>'
                f'<div class="idk-q">{ui.escape(run["question"] or "")}</div></div>', unsafe_allow_html=True)
    reasons = [r for s in (run.get("stage_results") or []) for r in s.get("reasons", [])]
    for r in reasons:
        st.markdown(f"- {r['detail']}")
    labels = {
        "HOLD_DUPLICATE_REVIEW": ("It's a new bill, continue", "It's a duplicate, reject"),
        "HOLD_VARIANCE": ("PO amended, approve", "Dispute with vendor"),
        "BLOCK_FRAUD_REVIEW": ("Vendor confirmed by call, release", "Not confirmed, reject"),
        "REQUEST_INFO": ("Resolved, approve", "Reject and return to vendor"),
    }.get(run["decision"], ("Approve", "Reject"))
    with st.form(f"review-{run['id']}", border=False):
        c1, c2 = st.columns(2)
        who = c1.text_input("Your name", placeholder="e.g. Priya, AP lead")
        why = c2.text_input("Reason (recorded on the case)", placeholder="e.g. Called vendor on master number; change confirmed")
        b1, b2, _ = st.columns([1, 1, 2])
        approve = b1.form_submit_button(labels[0], type="primary")
        reject = b2.form_submit_button(labels[1])
    if approve or reject:
        if not who.strip() or not why.strip():
            st.error("A name and a reason are both required. Every override is logged.")
        elif db.submit_review(run["id"], who.strip(), "approve" if approve else "reject", why.strip()):
            st.toast("Decision recorded.")
            st.rerun()
        else:
            st.warning("Someone already decided this case. Showing the latest state.")
            st.rerun()


def page_case():
    run_id = st.session_state.get("open_run") or st.query_params.get("run")
    runs = db.rows("select id, file_name, vendor_name, invoice_number, started_at from runs order by started_at desc limit 50")
    if not runs:
        st.title("Case file")
        st.info("No runs yet. Process an invoice on the New invoice page.")
        return
    ids = [r["id"] for r in runs]
    if run_id not in ids:
        run_id = ids[0]
    names = {r["id"]: f"{r['vendor_name'] or r['file_name']} · {r['invoice_number'] or '—'} · {r['started_at'][11:19]} UTC" for r in runs}
    pick = st.selectbox("Case", ids, index=ids.index(run_id), format_func=lambda i: names[i], label_visibility="collapsed")
    st.session_state["open_run"] = pick
    st.query_params["run"] = pick
    run = db.get_run(pick)
    events = db.get_events(pick)

    st.markdown('<div class="idk-eyebrow">Case file</div>', unsafe_allow_html=True)
    st.markdown(ui.header(run), unsafe_allow_html=True)

    if run["status"] == "failed":
        st.error(f"This run stopped: {run['error']}")
        st.page_link(PAGES["new"], label="Try another file", icon=":material/upload_file:")
    elif run["status"] == "running":
        st.info("This run is still in progress.")
        if st.button("Refresh"):
            st.rerun()
    else:
        st.markdown(ui.banner(run), unsafe_allow_html=True)
        if run["status"] == "awaiting_review":
            review_card(run)
        rv = db.get_review(pick)
        if rv:
            final = "approved" if rv["final_decision"] == "APPROVED_BY_REVIEWER" else "rejected"
            st.markdown(f'<div class="idk-card"><div class="idk-eyebrow">Human review</div>System recommended <b>{ui.escape(run["decision_label"])}</b>. '
                        f'<b>{ui.escape(rv["reviewer"])}</b> {final} it: “{ui.escape(rv["reason"])}”'
                        f'<div class="idk-sum">{rv["created_at"][:19].replace("T", " ")} UTC</div></div>', unsafe_allow_html=True)

    left, centre, right = st.columns([1.05, 1.6, 1.15], gap="large")
    with left:
        st.markdown('<div class="idk-eyebrow">Stages</div>', unsafe_allow_html=True)
        st.markdown(ui.timeline(events, pipeline.STAGES), unsafe_allow_html=True)
    with centre:
        states = ui.stage_states(events, pipeline.STAGES)
        available = [s for s, v in states.items() if v["state"] != "pending"]
        if available:
            default = next((s for s in available if states[s]["state"] in ("failed", "waiting")), None)
            default = default or next((s for s in available if states[s]["state"] == "warning"), available[-1])
            stage = st.radio("Stage", available, index=available.index(default), horizontal=True, label_visibility="collapsed", key=f"stage-{pick}")
            s = states[stage]
            st.markdown(f'<div class="idk-sub">{ui.escape(s["summary"])}</div>', unsafe_allow_html=True)
            if stage == "Extract":
                st.markdown('<div class="idk-legend"><span>✓ Verified: found in the page text and no reader disagrees</span>'
                            '<span>! Needs review: not located, or readers disagree</span><span>! Missing: a required field is not printed; left empty, never guessed</span>'
                            '<span>i Not printed: optional field absent</span></div>',
                            unsafe_allow_html=True)
            st.markdown(ui.checks_table((s["payload"] or {}).get("checks", []), extract=stage == "Extract"), unsafe_allow_html=True)
    with right:
        st.markdown('<div class="idk-eyebrow">The invoice</div>', unsafe_allow_html=True)
        n = run.get("page_count") or 0
        if n:
            page = st.radio("Page", list(range(1, n + 1)), horizontal=True, label_visibility="collapsed", key=f"page-{pick}") if n > 1 else 1
            img = os.path.join(db.FILES_DIR, pick, f"page-{page}.png")
            if os.path.exists(img):
                kinds = run.get("page_kinds") or []
                st.image(img, caption=f"Page {page} · {kinds[page - 1] if len(kinds) >= page else ''}")
            else:
                st.caption("The page image is no longer stored (the server restarted). The decision log above is intact.")


def page_dashboard():
    st.markdown('<div class="idk-eyebrow">Accounts payable</div>', unsafe_allow_html=True)
    top_l, top_r = st.columns([4, 1])
    top_l.title("Dashboard")
    with top_r:
        st.write("")
        reset_button("reset-dash")
    runs = db.rows("select * from runs order by started_at desc")
    done = [r for r in runs if r["status"] in ("completed", "awaiting_review")]
    auto = [r for r in done if r["decision"] in ("AUTO_APPROVE", "APPROVE_WITH_NOTE")]
    waiting = [r for r in runs if r["status"] == "awaiting_review"]
    on_hold = sum(r["gross_paise"] or 0 for r in waiting)
    c = st.columns(4)
    c[0].metric("Invoices processed", len(done))
    c[1].metric("Approved without a person", f"{len(auto)}" + (f" ({round(100 * len(auto) / len(done))}%)" if done else ""))
    c[2].metric("Waiting for a person", len(waiting))
    c[3].metric("Value on hold", engine.format_inr(on_hold))

    q_tab, all_tab, trend_tab = st.tabs(["Queue", "All runs", "Where it stops"])
    with q_tab:
        st.caption("Cases that need a person, oldest first. Select a row to open the case.")
        queue = sorted(waiting, key=lambda r: r["started_at"])
        if not queue:
            st.markdown('<div class="idk-empty">Nothing waiting. Every processed invoice has a decision.</div>', unsafe_allow_html=True)
        else:
            df = pd.DataFrame([{"Vendor": r["vendor_name"], "Invoice": r["invoice_number"], "Amount": engine.format_inr(r["gross_paise"] or 0),
                                "Status": r["decision_label"], "Owner": r["owner"], "Question": r["question"], "Age": age(r["started_at"])}
                               for r in queue])
            ev = st.dataframe(df, hide_index=True, on_select="rerun", selection_mode="single-row", key="queue-table")
            if ev.selection.rows:
                open_case(queue[ev.selection.rows[0]]["id"])
    with all_tab:
        f1, f2 = st.columns([3, 2])
        term = f1.text_input("Search vendor, invoice or file", key="search").strip().lower()
        labels = sorted({r["decision_label"] or r["status"] for r in runs})
        chosen = f2.multiselect("Status", labels, key="status-filter")
        shown = [r for r in runs if (not term or term in " ".join(str(r.get(k) or "") for k in ("vendor_name", "invoice_number", "file_name")).lower())
                 and (not chosen or (r["decision_label"] or r["status"]) in chosen)]
        if not shown:
            st.markdown('<div class="idk-empty">No runs match.</div>', unsafe_allow_html=True)
        else:
            durations = {r["run_id"]: r["ms"] for r in db.rows("select run_id, sum(duration_ms) ms from run_events group by run_id")}
            df = pd.DataFrame([{
                "Started (UTC)": r["started_at"][:19].replace("T", " "), "Vendor": r["vendor_name"] or r["file_name"],
                "Invoice": r["invoice_number"], "Amount": engine.format_inr(r["gross_paise"]) if r["gross_paise"] is not None else "",
                "Status": r["decision_label"] or r["status"],
                "Final": {"APPROVED_BY_REVIEWER": "Approved by reviewer", "REJECTED_BY_REVIEWER": "Rejected by reviewer"}.get(r["final_decision"], ""),
                "Reasons": ", ".join(json.loads(r["reason_codes"]) if r["reason_codes"] else []),
                "Took": f"{(durations.get(r['id']) or 0) / 1000:.1f} s", "Reader": r["reader"] or "",
                "Sample": "Yes" if r["is_sample"] else ""} for r in shown])
            ev = st.dataframe(df, hide_index=True, on_select="rerun", selection_mode="single-row", key="runs-table")
            if ev.selection.rows:
                open_case(shown[ev.selection.rows[0]]["id"])
    with trend_tab:
        if not done:
            st.markdown('<div class="idk-empty">Run a few invoices to see where the process stops.</div>', unsafe_allow_html=True)
        else:
            st.caption("Outcomes so far. The demo set is deliberately exception-heavy, so this is not a touchless rate to compare with benchmarks.")
            counts = pd.Series([r["decision_label"] for r in done]).value_counts().rename("Invoices")
            st.bar_chart(counts, horizontal=True)
            codes = pd.Series([c for r in done for c in (json.loads(r["reason_codes"]) if r["reason_codes"] else [])])
            if not codes.empty:
                st.caption("Reason codes raised")
                st.bar_chart(codes.value_counts().rename("Times raised"), horizontal=True)


def page_policy():
    ctx = db.load_context(pipeline.today_ist())
    p = ctx["policy"]
    st.markdown(f'<div class="idk-eyebrow">Operating procedure · version {p["version"]}</div>', unsafe_allow_html=True)
    st.title("Policy and assumptions")
    st.markdown('<div class="idk-sub">The written rules every decision follows. Each case records the version it ran against.</div>', unsafe_allow_html=True)
    pct, cap = p["price_pct"], engine.format_inr(p["price_cap_paise"])
    rules = [
        ("Price over PO", f"Allowed up to {pct}% of the line value, capped at {cap}. Exactly at the limit passes; above it holds for the buyer."),
        ("PO total, including earlier bills", f"Everything billed on a PO may exceed it by at most {pct}%, capped at {cap}. Earlier invoices count, even ones on hold."),
        ("Quantity", "Billed quantity, across all invoices, may not exceed what was ordered or what the goods receipt shows was received."),
        ("Footing and tax rounding", f"Lines must add up to the subtotal, and subtotal plus tax to the total, within {engine.format_inr(p['footing_paise'])}."),
        ("Tax type", "Same state: CGST + SGST in equal halves. Different state: IGST. Rates must be a current GST slab."),
        ("Auto-approve ceiling", f"Clean invoices up to {engine.format_inr(p['auto_ceiling_paise'])} are approved without a person; above that, sign-off by amount band."),
        ("Duplicates", f"Checked against {p['lookback_months']} months of invoices in every status: same file, same IRN, same number in the same year "
                       f"(rejected); near-identical number with the same amount, or same amount within {p['d4_window_days']} days (held for a person)."),
        ("Bank details", "Must match the vendor master. Any difference stops payment until the vendor is called on the number in the master."),
        ("Worst outcome wins", "Every reason is kept; the most serious one sets the status. Security beats duplicates, duplicates beat vendor queries."),
    ]
    for k, v in rules:
        a, b = st.columns([1, 3])
        a.markdown(f"**{k}**")
        b.markdown(v)
    st.subheader("Assumptions and limits")
    for line in [
        "Tolerances are design choices, editable per customer. No ERP publishes a default.",
        "A PO balance is reserved when an invoice is matched, even if it is on hold, so two invoices cannot both claim the same balance.",
        "Suffix variants such as -R are held for a person, not rejected, because they may be genuine revisions.",
        "Wording such as 'urgent' or 'bankers have changed' is logged but never blocks by itself; the bank comparison does.",
        "The reader may use AI, but only to transcribe. It sees page images, has no tools, and may answer 'not on document'. Rules in code decide.",
        "If the AI is unavailable, a local reader (text layer, or OCR for scans) takes over. Nothing is guessed to fill a gap.",
        "Not built yet: email intake, resolving implied POs (no PO number printed), ERP posting.",
        "Test data is synthetic and uses a single buyer. Demo storage resets if the server restarts; Reset demo data does the same on purpose.",
    ]:
        st.markdown(f"- {line}")
    st.subheader("Master data the rules read")
    with st.expander(f"Vendor master ({len(ctx['vendors'])})"):
        st.dataframe(pd.DataFrame(ctx["vendors"])[["id", "name", "gstin", "ifsc", "bank_account", "phone", "status"]], hide_index=True)
    with st.expander(f"Purchase orders ({len(ctx['pos'])}) and lines"):
        pol = pd.DataFrame(ctx["po_lines"])
        pol["qty"] = pol["qty_milli"].map(lambda m: "" if pd.isna(m) else int(m) // 1000)
        pol["unit price"] = pol["unit_price_paise"].map(lambda m: "" if pd.isna(m) else engine.format_inr(int(m)))
        pol["line value"] = pol["line_net_paise"].map(lambda m: engine.format_inr(int(m)))
        st.dataframe(pol[["po_number", "line_no", "description", "kind", "qty", "unit price", "line value"]], hide_index=True)
    with st.expander(f"Goods receipts ({len(ctx['grns'])})"):
        g = pd.DataFrame(db.rows("select * from goods_receipts"))
        g["qty received"] = g["qty_milli"] // 1000
        st.dataframe(g[["grn_number", "po_number", "line_no", "qty received"]], hide_index=True)
    with st.expander(f"Invoice ledger ({len(ctx['ledger'])})"):
        led = pd.DataFrame(ctx["ledger"])
        led["gross"] = led["gross_paise"].map(engine.format_inr)
        led["reserves PO balance"] = led["reserves_po"].map(lambda b: "Yes" if b else "No")
        led["seeded"] = led["is_seed"].map(lambda b: "Yes" if b else "")
        st.dataframe(led[["vendor_id", "invoice_number", "invoice_date", "gross", "po_number", "status", "service_period", "reserves PO balance", "seeded"]],
                     hide_index=True)


PAGES = {
    "new": st.Page(page_new, title="New invoice", icon=":material/upload_file:", url_path="new", default=True),
    "case": st.Page(page_case, title="Case file", icon=":material/fact_check:", url_path="case"),
    "dashboard": st.Page(page_dashboard, title="Dashboard", icon=":material/dashboard:", url_path="dashboard"),
    "policy": st.Page(page_policy, title="Policy", icon=":material/gavel:", url_path="policy"),
}
try:
    nav = st.navigation(list(PAGES.values()), position="top")
except TypeError:  # older Streamlit without top navigation
    nav = st.navigation(list(PAGES.values()))
nav.run()
