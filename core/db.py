"""SQLite storage: master data, the invoice ledger, runs and the append-only event log.

The live run view and the dashboard read only from these tables, so the screen
can never show progress that did not happen.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("INVOICE_DESK_DATA", os.path.join(ROOT, "data"))
DB_PATH = os.path.join(DATA_DIR, "invoice_desk.db")
FILES_DIR = os.path.join(DATA_DIR, "runs")

BUYER = {"name": "Vanashree Precision Components Pvt Ltd", "gstin": "29AABCV4821K1Z2", "site": "Peenya, Bengaluru"}

SCHEMA = """
create table if not exists vendor_master (id text primary key, name text not null, gstin text not null unique,
  bank_account text not null, ifsc text not null, phone text not null, status text not null default 'active');
create table if not exists po_header (po_number text primary key, vendor_id text not null references vendor_master(id),
  status text not null default 'open', net_paise integer not null, site text);
create table if not exists po_lines (po_number text not null, line_no integer not null, description text not null,
  kind text not null check (kind in ('goods','service')), qty_milli integer, unit_price_paise integer,
  line_net_paise integer not null, primary key (po_number, line_no));
create table if not exists goods_receipts (grn_number text primary key, po_number text not null, line_no integer not null,
  qty_milli integer not null);
create table if not exists policy (version text primary key, data text not null, active integer not null default 1);
create table if not exists runs (id text primary key, status text not null default 'running', current_stage text,
  file_name text, file_hash text, page_count integer, page_kinds text, reader text, is_sample integer not null default 0,
  vendor_name text, invoice_number text, gross_paise integer, decision text, decision_label text, reason_codes text,
  owner text, question text, explanation text, payable_now_paise integer, disputed_paise integer, final_decision text,
  extraction text, normalized text, stage_results text, policy_version text, error text, started_at text not null,
  ended_at text);
create table if not exists run_events (id integer primary key autoincrement, run_id text not null, seq integer not null,
  stage text not null, phase text not null, status text, summary text, payload text, duration_ms integer,
  created_at text not null, unique (run_id, seq));
create table if not exists reviews (id integer primary key autoincrement, run_id text not null, reviewer text not null,
  action text not null, original_decision text not null, final_decision text not null, reason text not null,
  created_at text not null);
create table if not exists invoice_ledger (id text primary key, run_id text, vendor_id text not null, invoice_number text not null,
  invoice_date text not null, gross_paise integer not null, net_paise integer not null, po_number text, irn text, file_hash text,
  status text not null, service_period text, lines text not null default '[]', reserves_po integer not null default 0,
  is_seed integer not null default 0, created_at text not null);
create table if not exists extraction_cache (file_hash text not null, reader text not null, extraction text not null,
  created_at text not null, primary key (file_hash, reader));
"""

VENDORS = [
    ("V-NIM", "Nimbus Office Systems Pvt Ltd", "29AAFCN7316P1ZS", "50200064417390", "NVBK0000412", "+91 80 5550 0117"),
    ("V-KSS", "Kaveri Site Services LLP", "29AAHCK6153R1ZO", "30118800421776", "NVBK0002219", "+91 80 5550 0129"),
    ("V-SIS", "Sahyadri Industrial Supplies Pvt Ltd", "27AADCS5942H1Z6", "50200011834472", "NVBK0001187", "+91 20 5550 0144"),
]
POS = [
    ("4500012101", "V-NIM", "open", 155000000, "Peenya"),
    ("4500012118", "V-KSS", "open", 72000000, "Peenya"),
    ("4500012144", "V-SIS", "open", 47000000, "Peenya"),
    ("4500012150", "V-SIS", "open", 47000000, "Peenya"),
]
PO_LINES = [
    ("4500012101", 1, "Business laptop, 14-inch, 16 GB / 512 GB SSD", "goods", 25000, 6200000, 155000000),
    ("4500012118", 1, "CCTV installation at Peenya", "service", None, None, 72000000),
    ("4500012144", 1, "Deep groove ball bearing 6205-2RS", "goods", 400000, 117500, 47000000),
    ("4500012150", 1, "Deep groove ball bearing 6206-ZZ", "goods", 400000, 117500, 47000000),
]
GRNS = [("GRN-88231", "4500012101", 1, 25000), ("GRN-88377", "4500012144", 1, 400000), ("GRN-88402", "4500012150", 1, 400000)]
SEED_LEDGER = [
    ("V-KSS", "KSS/0091", "2026-08-14", 28320000, 24000000, "4500012118", "AUTO_APPROVE", "Milestone 1, Aug 2026",
     [{"po_number": "4500012118", "line_no": 1, "qty_milli": 1000, "net_paise": 24000000}]),
    ("V-KSS", "KSS/0107", "2026-09-22", 28320000, 24000000, "4500012118", "AUTO_APPROVE", "Milestone 2, Sep 2026",
     [{"po_number": "4500012118", "line_no": 1, "qty_milli": 1000, "net_paise": 24000000}]),
]
POLICY_V1 = {"version": "v1", "price_pct": 1, "price_cap_paise": 500000, "footing_paise": 100, "tax_paise": 100,
             "auto_ceiling_paise": 250000000, "lookback_months": 18, "d4_window_days": 30}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@contextmanager
def conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("pragma journal_mode=wal")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    """Create tables and load the seed data once."""
    with conn() as c:
        c.executescript(SCHEMA)
        if c.execute("select count(*) from vendor_master").fetchone()[0] == 0:
            c.executemany("insert into vendor_master (id,name,gstin,bank_account,ifsc,phone) values (?,?,?,?,?,?)", VENDORS)
            c.executemany("insert into po_header values (?,?,?,?,?)", POS)
            c.executemany("insert into po_lines values (?,?,?,?,?,?,?)", PO_LINES)
            c.executemany("insert into goods_receipts values (?,?,?,?)", GRNS)
            c.execute("insert into policy values (?,?,1)", ("v1", json.dumps(POLICY_V1)))
            _insert_seed_ledger(c)


def _insert_seed_ledger(c):
    for v, num, d, gross, net, po, status, period, lines in SEED_LEDGER:
        c.execute("""insert into invoice_ledger (id,run_id,vendor_id,invoice_number,invoice_date,gross_paise,net_paise,po_number,irn,
                     file_hash,status,service_period,lines,reserves_po,is_seed,created_at) values (?,?,?,?,?,?,?,?,?,?,?,?,?,1,1,?)""",
                  (str(uuid.uuid4()), None, v, num, d, gross, net, po, None, None, status, period, json.dumps(lines), now()))


def reset_demo() -> dict:
    """Delete every run and every non-seed ledger row. Master data and the two seed bills stay."""
    with conn() as c:
        counts = {
            "runs": c.execute("select count(*) from runs").fetchone()[0],
            "ledger rows": c.execute("select count(*) from invoice_ledger where is_seed = 0").fetchone()[0],
        }
        # the extraction cache is kept on purpose: re-running a sample after a reset
        # should not spend another AI call on a file already read
        for t in ("reviews", "run_events", "runs"):
            c.execute(f"delete from {t}")
        c.execute("delete from invoice_ledger where is_seed = 0")
    shutil.rmtree(FILES_DIR, ignore_errors=True)
    return counts


# ---------- reads ----------
def rows(sql, args=()):
    with conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def row(sql, args=()):
    r = rows(sql, args)
    return r[0] if r else None


def load_context(today: str) -> dict:
    pol = row("select data from policy where active = 1 order by version desc limit 1")
    ledger = rows("select * from invoice_ledger")
    for r in ledger:
        r["lines"] = json.loads(r["lines"] or "[]")
        r["reserves_po"] = bool(r["reserves_po"])
    return {
        "buyer_gstin": BUYER["gstin"],
        "vendors": rows("select * from vendor_master"),
        "pos": rows("select * from po_header"),
        "po_lines": rows("select * from po_lines"),
        "grns": rows("select po_number, line_no, qty_milli from goods_receipts"),
        "ledger": ledger,
        "policy": json.loads(pol["data"]) if pol else dict(POLICY_V1),
        "today": today,
    }


def get_run(run_id):
    r = row("select * from runs where id = ?", (run_id,))
    if r:
        for k in ("explanation", "extraction", "normalized", "stage_results", "reason_codes", "page_kinds"):
            r[k] = json.loads(r[k]) if r.get(k) else None
    return r


def get_events(run_id, after_seq=0):
    ev = rows("select * from run_events where run_id = ? and seq > ? order by seq", (run_id, after_seq))
    for e in ev:
        e["payload"] = json.loads(e["payload"]) if e["payload"] else None
    return ev


def runs_started_since(iso: str) -> int:
    return row("select count(*) n from runs where started_at >= ?", (iso,))["n"]


# ---------- writes ----------
def create_run(run_id, file_name, is_sample):
    with conn() as c:
        c.execute("insert into runs (id, status, file_name, is_sample, started_at) values (?, 'running', ?, ?, ?)",
                  (run_id, file_name, 1 if is_sample else 0, now()))


def update_run(run_id, **fields):
    if not fields:
        return
    for k, v in list(fields.items()):
        if isinstance(v, (dict, list)):
            fields[k] = json.dumps(v)
    cols = ", ".join(f"{k} = ?" for k in fields)
    with conn() as c:
        c.execute(f"update runs set {cols} where id = ?", (*fields.values(), run_id))


def add_event(run_id, stage, phase, status=None, summary=None, payload=None, duration_ms=None):
    with conn() as c:
        seq = c.execute("select coalesce(max(seq), 0) + 1 from run_events where run_id = ?", (run_id,)).fetchone()[0]
        c.execute("insert into run_events (run_id, seq, stage, phase, status, summary, payload, duration_ms, created_at) values (?,?,?,?,?,?,?,?,?)",
                  (run_id, seq, stage, phase, status, summary, json.dumps(payload) if payload is not None else None, duration_ms, now()))
        c.execute("update runs set current_stage = ? where id = ?", (stage, run_id))
    return seq


def insert_ledger_row(run_id, lr: dict):
    with conn() as c:
        c.execute("""insert into invoice_ledger (id,run_id,vendor_id,invoice_number,invoice_date,gross_paise,net_paise,po_number,irn,file_hash,
                     status,service_period,lines,reserves_po,is_seed,created_at) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)""",
                  (str(uuid.uuid4()), run_id, lr["vendor_id"], lr["invoice_number"], lr["invoice_date"], lr["gross_paise"], lr["net_paise"],
                   lr["po_number"], lr["irn"], lr["file_hash"], lr["status"], lr["service_period"], json.dumps(lr["lines"]),
                   1 if lr["reserves_po"] else 0, now()))


def cached_extraction(file_hash, reader):
    r = row("select extraction from extraction_cache where file_hash = ? and reader = ?", (file_hash, reader))
    return json.loads(r["extraction"]) if r else None


def cache_extraction(file_hash, reader, extraction):
    with conn() as c:
        c.execute("insert or replace into extraction_cache values (?,?,?,?)", (file_hash, reader, json.dumps(extraction), now()))


def submit_review(run_id, reviewer, action, why) -> bool:
    """Apply a person's decision exactly once. Returns False if someone already decided."""
    final = "APPROVED_BY_REVIEWER" if action == "approve" else "REJECTED_BY_REVIEWER"
    with conn() as c:
        cur = c.execute("update runs set status = 'completed', final_decision = ?, ended_at = ? where id = ? and status = 'awaiting_review'",
                        (final, now(), run_id))
        if cur.rowcount == 0:
            return False
        original = c.execute("select decision from runs where id = ?", (run_id,)).fetchone()[0]
        c.execute("insert into reviews (run_id, reviewer, action, original_decision, final_decision, reason, created_at) values (?,?,?,?,?,?,?)",
                  (run_id, reviewer, action, original, final, why, now()))
        seq = c.execute("select coalesce(max(seq), 0) + 1 from run_events where run_id = ?", (run_id,)).fetchone()[0]
        verb = "approve" if action == "approve" else "reject"
        c.execute("insert into run_events (run_id, seq, stage, phase, status, summary, payload, created_at) values (?,?,?,?,?,?,?,?)",
                  (run_id, seq, "Human review", "finished", "passed", f"{reviewer} chose to {verb} (system said {original}): {why}",
                   json.dumps({"checks": [{"name": "Reviewer decision", "result": "info", "found": final, "note": why}], "reasons": []}), now()))
        if action == "approve":
            c.execute("update invoice_ledger set status = ? where run_id = ?", (final, run_id))
        else:
            c.execute("update invoice_ledger set status = ?, reserves_po = 0 where run_id = ?", (final, run_id))
    return True


def get_review(run_id):
    return row("select * from reviews where run_id = ? order by id desc limit 1", (run_id,))
