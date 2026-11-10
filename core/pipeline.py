"""The process itself: seven stages in a fixed order, each writing real events as it runs.

Receive and read -> Extract -> Validate -> Duplicate check -> PO match -> Security -> Decide

`on_event` is called after every event is written, so a screen can redraw from the
database while the run is still going.
"""
from __future__ import annotations

import os
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone

from . import db, engine, reader

IST = timezone(timedelta(hours=5, minutes=30))
STAGES = ["Receive and read", "Extract", "Validate", "Duplicate check", "PO match", "Security", "Decide"]
DAILY_CAP = 60
REQUIRED = ("invoice_number", "invoice_date", "vendor_gstin", "total")

FIELD_LABELS = {
    "doc_type": "Document type", "invoice_number": "Invoice number", "invoice_date": "Invoice date", "vendor_name": "Vendor name",
    "vendor_gstin": "Vendor GSTIN", "buyer_gstin": "Buyer GSTIN", "place_of_supply": "Place of supply", "po_number": "PO number",
    "irn": "IRN (e-invoice)", "service_period": "Service period", "subtotal": "Subtotal (taxable value)", "cgst": "CGST",
    "sgst": "SGST", "igst": "IGST", "total": "Total payable", "bank_account_no": "Bank account", "ifsc": "IFSC",
    "payment_note": "Payment note",
}


def today_ist() -> str:
    return datetime.now(IST).date().isoformat()


def runs_today() -> int:
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    return db.runs_started_since(since)


def run_invoice(pdf_bytes: bytes, file_name: str, is_sample: bool = False, gemini_key: str | None = None,
                gemini_model: str = "gemini-flash-latest", on_event=None, run_id: str | None = None) -> str:
    run_id = run_id or str(uuid.uuid4())
    db.init()
    db.create_run(run_id, file_name, is_sample)
    started_at = {}
    current = {"stage": None}

    def emit(stage, phase, status=None, summary=None, payload=None):
        if phase == "started":
            started_at[stage] = time.perf_counter()
            current["stage"] = stage
            db.add_event(run_id, stage, "started")
        else:
            dur = int((time.perf_counter() - started_at.get(stage, time.perf_counter())) * 1000)
            db.add_event(run_id, stage, "finished", status, summary, payload, dur)
        if on_event:
            on_event(run_id)

    try:
        # ---- 1. Receive and read (no AI)
        emit("Receive and read", "started")
        out_dir = os.path.join(db.FILES_DIR, run_id)
        got = reader.receive(pdf_bytes, out_dir)
        if not got["ok"]:
            emit("Receive and read", "finished", "failed", got["error"], {"checks": got["checks"], "reasons": []})
            db.update_run(run_id, status="failed", error=got["error"], file_hash=got["file_hash"], ended_at=db.now())
            return run_id
        pages = got["pages"]
        kinds = [p["kind"] for p in pages]
        db.update_run(run_id, file_hash=got["file_hash"], page_count=len(pages), page_kinds=kinds)
        kind_txt = ", ".join(sorted(set(k.lower() for k in kinds)))
        emit("Receive and read", "finished", "passed",
             f"{len(pages)} page{'s' if len(pages) > 1 else ''}, {kind_txt}. File fingerprint recorded.",
             {"checks": got["checks"], "reasons": []})

        # ---- 2. Extract (the only step that may use AI, and it only transcribes)
        emit("Extract", "started")
        pages = reader.page_texts(pages, pdf_bytes)
        local = reader.local_extract(pages, db.BUYER["gstin"])
        scanned = any(k == "Scanned image" for k in kinds)
        local_name = "Local reader (OCR, no AI)" if scanned else "Local reader (text layer, no AI)"
        notes, second = [], None
        x, reader_name = local, local_name
        if gemini_key:
            cache_key = f"gemini:{gemini_model}"
            cached = db.cached_extraction(got["file_hash"], cache_key)
            try:
                if cached:
                    x = cached
                    notes.append("Re-used the saved AI reading of this exact file, so no API call was spent.")
                else:
                    x = reader.gemini_extract(pages, gemini_key, gemini_model)
                    db.cache_extraction(got["file_hash"], cache_key, x)
                reader_name, second = f"Gemini ({gemini_model}), images only", local
            except Exception as e:  # the AI being down never stops the process
                x, reader_name = local, local_name
                notes.append(f"AI reader unavailable ({e}). Switched to the local reader; nothing was guessed.")
        ev = reader.evidence(x, pages, second)
        checks = []
        for e in ev:
            result = {"verified": "pass", "needs review": "warn", "missing": "warn"}[e["band"]]
            if e["band"] == "missing" and e["why"].startswith("Printed but"):
                result = "fail"
            elif e["band"] == "missing" and e["field"] not in REQUIRED:
                result, e["band"] = "info", "not printed"  # optional field, e.g. no IRN or no IGST on a same-state bill
            checks.append({"name": FIELD_LABELS[e["field"]], "result": result, "found": e["value"] or "—",
                           "note": ((e["where"] + ". ") if e["where"] else "") + e["why"][0].upper() + e["why"][1:], "band": e["band"]})
        for i, l in enumerate(x.get("lines", [])):
            bits = [b for b in (l.get("qty"), (f"@ {l['unit_price']}" if l.get("unit_price") else None), (f"= {l['amount']}" if l.get("amount") else None)) if b]
            checks.append({"name": f"Line {i + 1}", "result": "pass" if l.get("amount") else "warn",
                           "found": f"{l.get('description') or '(no description)'}: {' '.join(bits)}",
                           "note": "; ".join([f"HSN/SAC {l['hsn']}" if l.get("hsn") else ""] + l.get("notes", [])).strip("; ")})
        for n in notes:
            checks.append({"name": "Reader note", "result": "info", "found": n})
        verified = sum(1 for e in ev if e["band"] == "verified")
        review = sum(1 for e in ev if e["band"] == "needs review" or (e["band"] == "missing"))
        status = "warning" if review or any(c["result"] == "fail" for c in checks) else "passed"
        db.update_run(run_id, extraction=x, reader=reader_name)
        emit("Extract", "finished", status,
             f"{reader_name}: {verified} fields verified, {review} need review, {len(x.get('lines', []))} line item(s).",
             {"checks": checks, "reasons": [], "reader": reader_name})

        # ---- 3 to 7. Plain code decides
        ctx = db.load_context(today_ist())

        def engine_emit(stage, phase, result=None):
            if phase == "started":
                emit(stage, "started")
            else:
                emit(stage, "finished", result["status"], result["summary"], {"checks": result["checks"], "reasons": result["reasons"]})

        out = engine.process_invoice(x, got["file_hash"], ctx, engine_emit)
        n, d = out["normalized"], out["decision"]
        if out["ledger_row"]:
            db.insert_ledger_row(run_id, out["ledger_row"])
        db.update_run(
            run_id, status="awaiting_review" if d["needs_review"] else "completed", decision=d["status"],
            decision_label=d["label"], reason_codes=[r["code"] for r in d["reasons"]], owner=d["owner"], question=d["question"],
            explanation=d["explanation"], payable_now_paise=d["payable_now_paise"], disputed_paise=d["disputed_paise"],
            normalized={k: v for k, v in n.items() if k != "vendor"} | {"vendor_id": (n["vendor"] or {}).get("id")},
            stage_results=out["stages"], policy_version=d["policy_version"],
            vendor_name=(n["vendor"] or {}).get("name") or n["vendor_name"], invoice_number=n["invoice_number"],
            gross_paise=n["gross_paise"], ended_at=db.now(),
        )
        if on_event:
            on_event(run_id)
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        stage = current["stage"] or "Receive and read"
        db.add_event(run_id, stage, "finished", "failed", f"Stopped: {msg}",
                     {"checks": [{"name": "Error", "result": "fail", "found": msg, "note": traceback.format_exc(limit=2)[-400:]}], "reasons": []})
        db.update_run(run_id, status="failed", error=msg, ended_at=db.now())
        if on_event:
            on_event(run_id)
    return run_id
