"""HTML building blocks for the screens. Pure functions: data in, markup out."""
from __future__ import annotations

from html import escape

from .engine import format_inr

CSS = """
<style>
:root { --ink:#16181D; --muted:#5B616E; --line:#E4E6EA; --soft:#F6F7F5; --teal:#0F6E6E; --teal-soft:#E3F1F0;
        --ok:#1E7A46; --ok-soft:#E5F3EA; --warn:#9A5B00; --warn-soft:#FBF0DC; --bad:#B42318; --bad-soft:#FDECEA; }
.block-container { padding-top: 2.2rem; max-width: 1400px; }
h1, h2, h3 { letter-spacing: -0.01em; }
.idk-eyebrow { font-size: .74rem; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--teal); margin-bottom: .2rem; }
.idk-sub { color: var(--muted); margin-top: -.4rem; margin-bottom: 1.2rem; font-size: .98rem; }
.idk-chip { display:inline-block; padding: 2px 10px; border-radius: 999px; font-size: .78rem; font-weight: 600; border: 1px solid var(--line);
            background: var(--soft); color: var(--ink); margin-right: 6px; white-space: nowrap; }
.idk-chip.teal { background: var(--teal-soft); color: var(--teal); border-color: transparent; }
.idk-chip.ok { background: var(--ok-soft); color: var(--ok); border-color: transparent; }
.idk-chip.warn { background: var(--warn-soft); color: var(--warn); border-color: transparent; }
.idk-chip.bad { background: var(--bad-soft); color: var(--bad); border-color: transparent; }
.idk-chip.code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 500; font-size: .72rem; }
.idk-head { display:flex; flex-wrap: wrap; gap: 18px; align-items: baseline; padding: 4px 0 14px; border-bottom: 1px solid var(--line); margin-bottom: 16px; }
.idk-head .k { font-size: .72rem; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; }
.idk-head .v { font-size: 1.02rem; font-weight: 600; color: var(--ink); font-variant-numeric: tabular-nums; }
.idk-tl { list-style: none; margin: 0; padding: 0; position: relative; }
.idk-tl li { position: relative; padding: 0 0 16px 34px; }
.idk-tl li:before { content: ""; position: absolute; left: 11px; top: 24px; bottom: -2px; width: 2px; background: var(--line); }
.idk-tl li:last-child:before { display: none; }
.idk-dot { position: absolute; left: 0; top: 0; width: 24px; height: 24px; border-radius: 50%; display:flex; align-items:center; justify-content:center;
           font-size: 13px; font-weight: 700; border: 2px solid var(--line); background: #fff; color: var(--muted); }
.idk-dot.passed { background: var(--ok); border-color: var(--ok); color: #fff; }
.idk-dot.warning { background: var(--warn-soft); border-color: var(--warn); color: var(--warn); }
.idk-dot.failed { background: var(--bad); border-color: var(--bad); color: #fff; }
.idk-dot.waiting { background: var(--warn); border-color: var(--warn); color: #fff; }
.idk-dot.skipped { background: var(--soft); border-style: dashed; }
.idk-dot.running { border-color: var(--teal); color: var(--teal); animation: idkpulse 1s ease-in-out infinite; }
@keyframes idkpulse { 50% { box-shadow: 0 0 0 6px var(--teal-soft); } }
.idk-st { font-weight: 600; color: var(--ink); font-size: .95rem; }
.idk-word { font-size: .76rem; font-weight: 600; margin-left: 6px; }
.idk-word.passed { color: var(--ok); } .idk-word.warning, .idk-word.waiting { color: var(--warn); } .idk-word.failed { color: var(--bad); }
.idk-word.running { color: var(--teal); } .idk-word.skipped, .idk-word.pending { color: var(--muted); }
.idk-ms { font-size: .74rem; color: var(--muted); margin-left: 6px; font-variant-numeric: tabular-nums; }
.idk-sum { font-size: .84rem; color: var(--muted); margin-top: 2px; line-height: 1.35; }
.idk-tbl { width: 100%; border-collapse: collapse; font-size: .86rem; }
.idk-tbl th { text-align: left; font-size: .72rem; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); font-weight: 600;
              padding: 6px 8px; border-bottom: 1px solid var(--line); }
.idk-tbl td { padding: 8px; border-bottom: 1px solid var(--line); vertical-align: top; font-variant-numeric: tabular-nums; }
.idk-tbl td.res { white-space: nowrap; font-weight: 600; }
.idk-tbl td.note { color: var(--muted); font-size: .8rem; }
.res-pass { color: var(--ok); } .res-warn { color: var(--warn); } .res-fail { color: var(--bad); } .res-info { color: var(--muted); }
.idk-banner { border-radius: 12px; padding: 18px 20px; margin: 4px 0 18px; border: 1px solid var(--line); background: #fff; }
.idk-banner.ok { border-left: 6px solid var(--ok); } .idk-banner.warn { border-left: 6px solid var(--warn); } .idk-banner.bad { border-left: 6px solid var(--bad); }
.idk-banner .label { font-size: 1.45rem; font-weight: 700; color: var(--ink); margin-bottom: 6px; }
.idk-grid { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 14px; margin-top: 12px; }
@media (max-width: 900px) { .idk-grid { grid-template-columns: 1fr; } }
.idk-grid .k { font-size: .72rem; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); font-weight: 600; margin-bottom: 3px; }
.idk-grid .v { font-size: .9rem; color: var(--ink); line-height: 1.4; }
.idk-money { display:flex; gap: 28px; margin-top: 12px; padding-top: 12px; border-top: 1px dashed var(--line); }
.idk-money .k { font-size: .72rem; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); }
.idk-money .v { font-size: 1.25rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.idk-q { font-size: 1.12rem; font-weight: 600; color: var(--ink); line-height: 1.45; margin: 4px 0 8px; }
.idk-card { border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; background: var(--soft); margin-bottom: 14px; }
.idk-legend span { margin-right: 14px; font-size: .78rem; color: var(--muted); }
.idk-empty { color: var(--muted); font-size: .9rem; padding: 12px 0; }
.idk-sample { padding: 10px 0; border-bottom: 1px solid var(--line); }
.idk-sample .t { font-weight: 600; } .idk-sample .d { color: var(--muted); font-size: .85rem; }
</style>
"""

ICON = {"passed": "✓", "warning": "!", "failed": "✕", "waiting": "●", "skipped": "–", "running": "◐", "pending": ""}
WORD = {"passed": "Passed", "warning": "Warning", "failed": "Failed", "waiting": "Needs a person", "skipped": "Skipped",
        "running": "Running", "pending": "Waiting"}
RES = {"pass": ("✓", "Pass"), "warn": ("!", "Warn"), "fail": ("✕", "Fail"), "info": ("i", "Info")}

APPROVED = {"AUTO_APPROVE", "APPROVE_WITH_NOTE", "APPROVED_BY_REVIEWER"}
BAD = {"BLOCK_FRAUD_REVIEW", "REJECT_DUPLICATE", "REJECT_INVALID", "REJECTED_BY_REVIEWER"}


def tone(decision: str | None) -> str:
    if decision in APPROVED:
        return "ok"
    if decision in BAD:
        return "bad"
    return "warn"


def stage_states(events: list, stages: list) -> dict:
    """Derive each stage's state from real events only."""
    st = {s: {"state": "pending", "summary": "", "ms": None, "payload": None} for s in stages}
    for e in events:
        s = st.setdefault(e["stage"], {"state": "pending", "summary": "", "ms": None, "payload": None})
        if e["phase"] == "started":
            s["state"] = "running"
        else:
            s["state"] = e["status"] or "passed"
            s["summary"] = e["summary"] or ""
            s["ms"] = e["duration_ms"]
            s["payload"] = e["payload"]
    return st


def timeline(events: list, stages: list) -> str:
    st = stage_states(events, stages)
    order = list(stages) + [k for k in st if k not in stages]
    items = []
    for name in order:
        s = st[name]
        state = s["state"]
        ms = ""
        if s["ms"] is not None:
            ms = f"{s['ms'] / 1000:.1f} s" if s["ms"] >= 1000 else f"{s['ms']} ms"
        items.append(
            f'<li><div class="idk-dot {state}">{ICON.get(state, "")}</div>'
            f'<div><span class="idk-st">{escape(name)}</span><span class="idk-word {state}">{WORD.get(state, state)}</span>'
            f'<span class="idk-ms">{ms}</span></div>'
            f'<div class="idk-sum">{escape(s["summary"])}</div></li>')
    return f'<ul class="idk-tl">{"".join(items)}</ul>'


def checks_table(checks: list, extract: bool = False) -> str:
    if not checks:
        return '<div class="idk-empty">No checks recorded for this stage yet.</div>'
    if extract:
        head = "<tr><th>Evidence</th><th>Field</th><th>What the invoice says</th><th>Where it was found, and why it is trusted</th></tr>"
    else:
        head = "<tr><th>Result</th><th>Check</th><th>Expected</th><th>Found</th><th>Note</th></tr>"
    rows = []
    for c in checks:
        icon, word = RES.get(c.get("result"), ("", c.get("result", "")))
        cls = f"res-{c.get('result')}"
        if extract:
            band = c.get("band")
            word = {"verified": "Verified", "needs review": "Needs review", "missing": "Missing",
                    "not printed": "Not printed"}.get(band, word)
            rows.append(f'<tr><td class="res {cls}">{icon} {word}</td><td>{escape(c["name"])}</td>'
                        f'<td>{escape(str(c.get("found", "")))}</td><td class="note">{escape(str(c.get("note", "") or ""))}</td></tr>')
        else:
            rows.append(f'<tr><td class="res {cls}">{icon} {word}</td><td>{escape(c["name"])}</td>'
                        f'<td>{escape(str(c.get("expected", "") or ""))}</td><td>{escape(str(c.get("found", "") or ""))}</td>'
                        f'<td class="note">{escape(str(c.get("note", "") or ""))}</td></tr>')
    return f'<table class="idk-tbl">{head}{"".join(rows)}</table>'


def header(run: dict) -> str:
    badge = '<span class="idk-chip teal">Live</span>' if run["status"] == "running" else '<span class="idk-chip">Saved run</span>'
    amt = format_inr(run["gross_paise"]) if run.get("gross_paise") is not None else "—"
    cells = [("File", run.get("file_name") or "—"), ("Vendor", run.get("vendor_name") or "—"),
             ("Invoice", run.get("invoice_number") or "—"), ("Amount", amt), ("Reader", run.get("reader") or "—")]
    inner = "".join(f'<div><div class="k">{k}</div><div class="v">{escape(str(v))}</div></div>' for k, v in cells)
    return f'<div class="idk-head">{inner}<div>{badge}</div></div>'


def banner(run: dict) -> str:
    ex = run.get("explanation") or {}
    t = tone(run.get("decision"))
    codes = "".join(f'<span class="idk-chip code">{escape(c)}</span>' for c in (run.get("reason_codes") or []))
    parts = [("What it checked", ex.get("checked")), ("What it found", ex.get("found")), ("What it did", ex.get("did")),
             ("Who needs to act", ex.get("who"))]
    if run.get("status") == "awaiting_review":  # the review card below carries the question
        parts[3] = ("Who needs to act", run.get("owner"))
    grid = '<div class="idk-grid">' + "".join(f'<div><div class="k">{k}</div><div class="v">{escape(v or "")}</div></div>' for k, v in parts) + "</div>"
    money = ""
    if (run.get("payable_now_paise") or 0) > 0 or (run.get("disputed_paise") or 0) > 0:
        money = (f'<div class="idk-money"><div><div class="k">Payable now</div><div class="v">{format_inr(run.get("payable_now_paise") or 0)}</div></div>'
                 f'<div><div class="k">Disputed</div><div class="v">{format_inr(run.get("disputed_paise") or 0)}</div></div></div>')
    return (f'<div class="idk-banner {t}"><div class="idk-eyebrow">Decision · policy {escape(run.get("policy_version") or "")}</div>'
            f'<div class="label">{escape(run.get("decision_label") or "")}</div><div>{codes}</div>{grid}{money}</div>')


def status_chip(decision: str | None, label: str | None, final: str | None = None) -> str:
    if final:
        return f'<span class="idk-chip {tone(final)}">{"Approved by reviewer" if final == "APPROVED_BY_REVIEWER" else "Rejected by reviewer"}</span>'
    return f'<span class="idk-chip {tone(decision)}">{escape(label or "—")}</span>'
