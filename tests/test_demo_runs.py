"""Runs the five demo invoices in order on a fresh ledger and checks every expected outcome.

    python tests/test_demo_runs.py

Uses the local reader (no API key), so it is free and gives the same answer every time.
The order matters: Run 2 must see Run 1, and Run 3 builds on the two seeded Kaveri bills.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["INVOICE_DESK_DATA"] = tempfile.mkdtemp(prefix="invoice-desk-test-")

from core import db, engine, pipeline  # noqa: E402

EXPECTED = [
    ("1_happy_path_NIM-0417.pdf", "AUTO_APPROVE", {"payable_now_paise": 182900000, "gross_paise": 182900000}, {}),
    ("2_duplicate_scan_NIM-417-R.pdf", "HOLD_DUPLICATE_REVIEW", {"payable_now_paise": 0}, {"PO match": "skipped", "Duplicate check": "failed"}),
    ("3_cumulative_overrun_KSS-0124.pdf", "HOLD_VARIANCE", {"payable_now_paise": 28320000, "disputed_paise": 2478000}, {}),
    ("4_bank_change_SIS-2291.pdf", "BLOCK_FRAUD_REVIEW", {"payable_now_paise": 0}, {"PO match": "passed", "Security": "failed"}),
    ("5_control_close_but_ok_SIS-2307.pdf", "APPROVE_WITH_NOTE", {"payable_now_paise": 55648800}, {"PO match": "warning"}),
]

APPROVED = {"AUTO_APPROVE", "APPROVE_WITH_NOTE"}


def main():
    db.init()
    failures, false_approvals = 0, 0
    for fname, want, numbers, stage_want in EXPECTED:
        data = open(os.path.join(ROOT, "samples", fname), "rb").read()
        run_id = pipeline.run_invoice(data, fname, is_sample=True)
        r = db.get_run(run_id)
        events = db.get_events(run_id)
        finished = {e["stage"]: e["status"] for e in events if e["phase"] == "finished"}
        ok = r["status"] != "failed" and r["decision"] == want
        for k, v in numbers.items():
            ok &= r[k] == v
        for k, v in stage_want.items():
            ok &= finished.get(k) == v
        ok &= all(s in finished for s in pipeline.STAGES)
        if r["decision"] in APPROVED and want not in APPROVED:
            false_approvals += 1
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {fname:38} {r['decision']!s:22} payable {engine.format_inr(r['payable_now_paise'] or 0):>12}"
              f"  disputed {engine.format_inr(r['disputed_paise'] or 0):>9}  reader: {r['reader']}")
        if not ok:
            print("      error:", r["error"], "| stages:", finished)
        print("      question:", r["question"])
    # re-sending Run 1's exact file must be caught as a duplicate (D0)
    data = open(os.path.join(ROOT, "samples", EXPECTED[0][0]), "rb").read()
    r = db.get_run(pipeline.run_invoice(data, "resend.pdf"))
    ok = r["decision"] == "REJECT_DUPLICATE"
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'}  re-upload of Run 1 file              {r['decision']}")
    # a reviewer decision applies exactly once
    run3 = db.rows("select id from runs where file_name like '3_%'")[0]["id"]
    first, second = db.submit_review(run3, "Test", "reject", "dispute"), db.submit_review(run3, "Test", "approve", "again")
    ok = first and not second
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'}  review applies once (second click refused)")
    # reset returns to the seed state
    db.reset_demo()
    ok = db.row("select count(*) n from invoice_ledger")["n"] == 2 and db.row("select count(*) n from runs")["n"] == 0
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'}  reset keeps only the 2 seeded Kaveri bills")
    print(f"\n{failures} failure(s), {false_approvals} false approval(s)")
    sys.exit(1 if failures or false_approvals else 0)


if __name__ == "__main__":
    main()
