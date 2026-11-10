"""Deterministic invoice decisions. No AI, no network, no database.

A line-for-line Python port of decisionEngine.ts. The AI (or the local reader)
only transcribes the invoice into an `Extraction` dict. Everything below is
plain code, so the same input always gives the same decision.

All money is integer paise. All quantities are integer thousandths ("milli").
JavaScript's Math.round (half rounds up) is reproduced with js_round so the
numbers match the TypeScript version exactly.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from fractions import Fraction
from math import floor

FIELD_NAMES = [
    "doc_type", "invoice_number", "invoice_date", "vendor_name", "vendor_gstin", "buyer_gstin",
    "place_of_supply", "po_number", "irn", "service_period", "subtotal", "cgst", "sgst", "igst",
    "total", "bank_account_no", "ifsc", "payment_note",
]

DEFAULT_POLICY = {
    "version": "v1", "price_pct": 1, "price_cap_paise": 500000, "footing_paise": 100, "tax_paise": 100,
    "auto_ceiling_paise": 250000000, "lookback_months": 18, "d4_window_days": 30,
}

STATUS_META = {
    "BLOCK_FRAUD_REVIEW": {"rank": 1, "label": "Stopped for a security check", "owner": "Controller / vendor-master team", "review": True},
    "REJECT_DUPLICATE": {"rank": 2, "label": "Already processed", "owner": "AP", "review": False},
    "HOLD_DUPLICATE_REVIEW": {"rank": 3, "label": "Possible duplicate", "owner": "AP", "review": True},
    "REJECT_INVALID": {"rank": 4, "label": "Not a payable invoice", "owner": "AP", "review": False},
    "REQUEST_INFO": {"rank": 5, "label": "Needs something from the vendor", "owner": "Vendor (via AP)", "review": True},
    "HOLD_VARIANCE": {"rank": 6, "label": "Doesn't match the PO", "owner": "Buyer", "review": True},
    "HOLD_AWAITING_RECEIPT": {"rank": 7, "label": "Billed before delivery", "owner": "Warehouse / requester", "review": True},
    "ROUTE_APPROVER": {"rank": 8, "label": "Needs sign-off", "owner": "Approver per grid", "review": True},
    "APPROVE_WITH_NOTE": {"rank": 9, "label": "Approved, with a recorded note", "owner": "None", "review": False},
    "AUTO_APPROVE": {"rank": 10, "label": "Approved", "owner": "None", "review": False},
}


# ---------- small helpers ----------
def js_round(x) -> int:
    """JavaScript Math.round: halves round towards +infinity."""
    return floor(Fraction(x) + Fraction(1, 2))


def check(name, result, expected=None, found=None, note=None) -> dict:
    c = {"name": name, "result": result}
    if expected is not None:
        c["expected"] = expected
    if found is not None:
        c["found"] = found
    if note is not None:
        c["note"] = note
    return c


def reason(code, status, detail) -> dict:
    return {"code": code, "status": status, "detail": detail}


# ---------- formatting and parsing ----------
def format_inr(paise: int) -> str:
    neg = paise < 0
    a = abs(int(paise))
    r, p = a // 100, a % 100
    s = str(r)
    last3, rest = s[-3:], s[:-3]
    body = (re.sub(r"\B(?=(\d{2})+(?!\d))", ",", rest) + "," + last3) if rest else last3
    return ("-₹" if neg else "₹") + body + (("." + str(p).zfill(2)) if p else "")


def parse_money_to_paise(raw):
    if not raw:
        return None
    s = raw.replace("/-", "")
    s = re.sub(r"(rs\.?|inr|₹)", "", s, flags=re.I)
    s = re.sub(r"\s|,", "", s)
    neg = bool(re.fullmatch(r"\(.*\)", s)) or s.startswith("-")
    s = re.sub(r"[()\-]", "", s)
    if not re.fullmatch(r"\d+(\.\d{1,2})?", s):
        return None
    r, _, p = s.partition(".")
    v = int(r) * 100 + int((p + "00")[:2])
    return -v if neg else v


def parse_qty_milli(raw):
    if not raw:
        return None
    m = re.search(r"\d+(\.\d{1,3})?", raw.replace(",", ""))
    if not m:
        return None
    w, _, f = m.group(0).partition(".")
    return int(w) * 1000 + int((f + "000")[:3])


MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _iso(y, m, d):
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def parse_date(raw):
    if not raw:
        return None
    s = raw.strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return _iso(int(m[1]), int(m[2]), int(m[3]))
    m = re.match(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})$", s)
    if m:  # Indian DD/MM
        y = int(m[3])
        return _iso(2000 + y if y < 100 else y, int(m[2]), int(m[1]))
    m = re.match(r"^(\d{1,2})(?:st|nd|rd|th)?[\s\-]+([A-Za-z]{3,9})[\s\-,]+(\d{2,4})$", s)
    if m:
        mo = MONTHS.get(m[2][:3].lower())
        y = int(m[3])
        return _iso(2000 + y if y < 100 else y, mo, int(m[1])) if mo else None
    m = re.match(r"^([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})$", s)
    if m:
        mo = MONTHS.get(m[1][:3].lower())
        return _iso(int(m[3]), mo, int(m[2])) if mo else None
    return None


def financial_year(iso: str) -> str:
    y, m = int(iso[:4]), int(iso[5:7])
    return f"{y}-{y + 1}" if m >= 4 else f"{y - 1}-{y}"


GSTIN_RE = r"^\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]Z[0-9A-Z]$"


def gstin_valid(g) -> bool:
    if not g or not re.match(GSTIN_RE, g):
        return False
    C = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    total = 0
    for i in range(14):
        p = C.index(g[i]) * (2 if i % 2 else 1)
        total += p // 36 + p % 36
    return C[(36 - total % 36) % 36] == g[14]


def strict_number(n: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", n.upper())


def loose_number(n: str) -> str:
    no_suffix = re.sub(r"[\s\-_/](R|REV|REVISED|DUP|DUPLICATE|COPY|A|B)$", "", n.upper().strip())
    s = strict_number(no_suffix)
    return s.replace("O", "0").replace("I", "1").replace("L", "1").replace("S", "5").replace("B", "8")


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i]
        for j in range(1, len(b) + 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (0 if a[i - 1] == b[j - 1] else 1)))
        prev = cur
    return prev[len(b)]


_STOP = {"pvt", "ltd", "private", "limited", "the", "and", "llp"}


def _tokens(s: str) -> set:
    return {t for t in re.split(r"\s+", re.sub(r"[^a-z0-9 ]", " ", s.lower())) if len(t) > 2 and t not in _STOP}


def overlap(a: str, b: str) -> float:
    A, B = _tokens(a), _tokens(b)
    if not A or not B:
        return 0
    return len(A & B) / min(len(A), len(B))


def _days_between(a: str, b: str) -> float:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def _months_ago(iso: str, months: int) -> str:
    d = date.fromisoformat(iso)
    total = d.year * 12 + (d.month - 1) - months
    y, m = divmod(total, 12)
    # JavaScript setUTCMonth lets the day overflow into the next month
    return (date(y, m + 1, 1) + timedelta(days=d.day - 1)).isoformat()


def _clean(v):
    return re.sub(r"\s", "", v).upper() if v else None


def _tolerance(base_net: int, p: dict) -> int:
    return min((base_net * p["price_pct"]) // 100, p["price_cap_paise"])


def _state_of(place, buyer_gstin: str) -> str:
    m = re.search(r"\b(\d{2})\b", place or "")
    return m[1] if m else buyer_gstin[:2]


def _stage_status(reasons, checks) -> str:
    if any(c["result"] == "fail" for c in checks) or any(STATUS_META[r["status"]]["rank"] <= 8 for r in reasons):
        return "failed"
    if reasons or any(c["result"] == "warn" for c in checks):
        return "warning"
    return "passed"


# ---------- stage 3: validate ----------
def validate(x: dict, ctx: dict):
    checks, reasons, p = [], [], ctx["policy"]
    fields = x.get("fields", {})

    def get(k):
        f = fields.get(k)
        if f and f.get("status") == "found":
            return (f.get("raw_text") or "").strip() or None
        return None

    missing = [k for k in ("invoice_number", "invoice_date", "total") if not get(k)]
    doc_type = get("doc_type")
    if doc_type and re.search(r"(pro.?forma|quotation|estimate|delivery challan)", doc_type, re.I):
        reasons.append(reason("NOT_PAYABLE_DOC", "REJECT_INVALID", f'Document is a "{doc_type}", not a tax invoice.'))
        checks.append(check("Document type", "fail", expected="Tax invoice", found=doc_type))
    else:
        checks.append(check("Document type", "pass", found=doc_type or "Invoice"))
    if missing:
        pretty = ", ".join(m.replace("_", " ") for m in missing)
        reasons.append(reason("MISSING_FIELD", "REQUEST_INFO", f"Not found on the document: {pretty}. Left empty, not guessed."))
        checks.append(check("Required fields", "fail", expected="Invoice number, date, total", found=f"Missing: {pretty}"))
    else:
        checks.append(check("Required fields", "pass", found="Invoice number, date and total all present"))

    vendor_gstin = _clean(get("vendor_gstin"))
    vendor_name = get("vendor_name")
    vendor = next((v for v in ctx["vendors"] if v["gstin"] == vendor_gstin), None)
    if vendor:
        checks.append(check("Vendor in master", "pass", found=vendor["name"]))
    else:
        near = next((v for v in ctx["vendors"] if overlap(v["name"], vendor_name) >= 0.6), None) if vendor_name else None
        if near:
            checks.append(check("Vendor in master", "warn", expected=near["gstin"], found=f"{vendor_name}, {vendor_gstin or 'no GSTIN'}",
                                note="Name resembles a known vendor but the tax ID differs. Security decides."))
            vendor = None
        else:
            reasons.append(reason("VENDOR_NOT_FOUND", "REQUEST_INFO", f"{vendor_name or 'This vendor'} is not in the approved vendor master."))
            checks.append(check("Vendor in master", "fail", found=vendor_name or "Unknown"))
    if vendor_gstin and not gstin_valid(vendor_gstin):
        reasons.append(reason("INVALID_GSTIN", "REQUEST_INFO", f"Vendor GSTIN {vendor_gstin} fails its checksum."))
        checks.append(check("GSTIN checksum", "fail", found=vendor_gstin))
    elif vendor_gstin:
        checks.append(check("GSTIN checksum", "pass", found=vendor_gstin))
    buyer = _clean(get("buyer_gstin"))
    if buyer and buyer != ctx["buyer_gstin"]:
        reasons.append(reason("WRONG_BUYER", "REJECT_INVALID", f"Billed to GSTIN {buyer}, which is not this company."))
        checks.append(check("Billed to us", "fail", expected=ctx["buyer_gstin"], found=buyer))
    elif buyer:
        checks.append(check("Billed to us", "pass", found=buyer))

    lines = [{"description": l.get("description") or "", "qty_milli": parse_qty_milli(l.get("qty")),
              "unit_price_paise": parse_money_to_paise(l.get("unit_price")), "amount_paise": parse_money_to_paise(l.get("amount"))}
             for l in x.get("lines", [])]
    if not lines:
        reasons.append(reason("NO_LINE_ITEMS", "REQUEST_INFO", "No line items could be read, so the invoice cannot be matched to a PO."))
        checks.append(check("Line items", "fail", found="None"))
    for i, l in enumerate(lines):
        if l["amount_paise"] is None:
            reasons.append(reason("LINE_AMOUNT_MISSING", "REQUEST_INFO", f"Line {i + 1} has no readable amount."))
            checks.append(check(f"Line {i + 1} amount", "fail", found="Missing"))
    line_sum = sum(l["amount_paise"] or 0 for l in lines)
    cgst = parse_money_to_paise(get("cgst")) or 0
    sgst = parse_money_to_paise(get("sgst")) or 0
    igst = parse_money_to_paise(get("igst")) or 0
    sub = parse_money_to_paise(get("subtotal"))
    net = sub if sub is not None else (line_sum if lines else None)
    gross = parse_money_to_paise(get("total"))
    dt = parse_date(get("invoice_date"))
    if get("invoice_date") and not dt:
        reasons.append(reason("UNPARSEABLE_DATE", "REQUEST_INFO", f'Could not read "{get("invoice_date")}" as a date.'))
        checks.append(check("Invoice date", "fail", found=get("invoice_date")))

    for i, l in enumerate(lines):
        if l["qty_milli"] is not None and l["unit_price_paise"] is not None and l["amount_paise"] is not None:
            calc = js_round(Fraction(l["qty_milli"] * l["unit_price_paise"], 1000))
            if abs(calc - l["amount_paise"]) > p["footing_paise"]:
                reasons.append(reason("LINE_ARITHMETIC", "REQUEST_INFO",
                                      f"Line {i + 1}: quantity × price is {format_inr(calc)}, but the invoice prints {format_inr(l['amount_paise'])}."))
                checks.append(check(f"Line {i + 1} arithmetic", "fail", expected=format_inr(calc), found=format_inr(l["amount_paise"])))
            else:
                checks.append(check(f"Line {i + 1} arithmetic", "pass", expected=format_inr(calc), found=format_inr(l["amount_paise"])))
    if net is not None and lines:
        ok = abs(net - line_sum) <= p["footing_paise"]
        checks.append(check("Lines add up to subtotal", "pass" if ok else "fail", expected=format_inr(line_sum), found=format_inr(net)))
        if not ok:
            reasons.append(reason("FOOTING_ERROR", "REQUEST_INFO", f"Lines add up to {format_inr(line_sum)}, but the subtotal says {format_inr(net)}."))
    if net is not None and gross is not None:
        calc = net + cgst + sgst + igst
        ok = abs(calc - gross) <= p["footing_paise"]
        checks.append(check("Subtotal + tax = total", "pass" if ok else "fail", expected=format_inr(calc), found=format_inr(gross)))
        if not ok:
            reasons.append(reason("TOTAL_MISMATCH", "REQUEST_INFO", f"Subtotal plus tax is {format_inr(calc)}, but the total says {format_inr(gross)}."))
        tax = cgst + sgst + igst
        if net > 0:
            slab = next((r for r in (0, 5, 18, 40) if abs(js_round(Fraction(net * r, 100)) - tax) <= p["tax_paise"] * 2), None)
            old = next((r for r in (12, 28) if abs(js_round(Fraction(net * r, 100)) - tax) <= p["tax_paise"] * 2), None)
            if slab is not None:
                checks.append(check("GST rate", "pass", found=f"{slab}% of {format_inr(net)} = {format_inr(tax)}"))
            elif old is not None:
                reasons.append(reason("OLD_GST_RATE", "APPROVE_WITH_NOTE", f"Tax works out to {old}%, a rate removed for most goods from 22 Sep 2025."))
                checks.append(check("GST rate", "warn", found=f"{old}%"))
            else:
                reasons.append(reason("TAX_RATE_UNRECOGNISED", "REQUEST_INFO", f"Tax of {format_inr(tax)} on {format_inr(net)} is not a valid GST rate."))
                checks.append(check("GST rate", "fail", found=format_inr(tax)))
    if abs(cgst - sgst) > p["tax_paise"]:
        reasons.append(reason("CGST_SGST_UNEQUAL", "REQUEST_INFO", f"CGST {format_inr(cgst)} and SGST {format_inr(sgst)} should be equal."))
        checks.append(check("CGST equals SGST", "fail", found=f"{format_inr(cgst)} / {format_inr(sgst)}"))
    if vendor_gstin:
        intra = vendor_gstin[:2] == _state_of(get("place_of_supply"), ctx["buyer_gstin"])
        uses_igst, uses_split = igst > 0, (cgst > 0 or sgst > 0)
        ok = (not uses_igst and not uses_split) or ((uses_split and not uses_igst) if intra else (uses_igst and not uses_split))
        checks.append(check("Correct tax type", "pass" if ok else "fail",
                            expected="Same state: CGST + SGST" if intra else "Different state: IGST",
                            found="IGST" if uses_igst else "CGST + SGST"))
        if not ok:
            reasons.append(reason("WRONG_TAX_HEAD", "REQUEST_INFO",
                                  "Same-state supply must charge CGST + SGST, but this invoice charges IGST." if intra
                                  else "Inter-state supply must charge IGST, but this invoice charges CGST + SGST."))
    po_raw = get("po_number")
    normalized = {
        "invoice_number": get("invoice_number"), "invoice_date": dt, "vendor": vendor, "vendor_name": vendor_name,
        "vendor_gstin": vendor_gstin, "po_number": re.sub(r"\s", "", po_raw) if po_raw else None, "irn": get("irn"),
        "service_period": get("service_period"), "net_paise": net, "gross_paise": gross, "cgst": cgst, "sgst": sgst,
        "igst": igst, "bank_account": _clean(get("bank_account_no")), "ifsc": _clean(get("ifsc")), "lines": lines,
    }
    status = _stage_status(reasons, checks)
    summary = (f"All {len(checks)} checks passed. The numbers add up and the tax is right." if status == "passed"
               else (" ".join(r["detail"] for r in reasons) or "Passed with warnings."))
    return normalized, {"stage": "Validate", "status": status, "checks": checks, "reasons": reasons, "summary": summary}


# ---------- stage 4: duplicates ----------
def check_duplicates(n: dict, file_hash, ctx: dict):
    cutoff = _months_ago(ctx["today"], ctx["policy"]["lookback_months"])
    rows = [r for r in ctx["ledger"] if r["invoice_date"] >= cutoff]
    checks, reasons, hit = [], [], None
    num, vid = n["invoice_number"], (n["vendor"] or {}).get("id")
    gross, inv_date = n["gross_paise"], n["invoice_date"]
    window = ctx["policy"]["d4_window_days"]

    def first(pred):
        return next((r for r in rows if pred(r)), None)

    tiers = [
        ("D0 Same file", lambda: first(lambda r: r.get("file_hash") == file_hash) if file_hash else None, "REJECT_DUPLICATE",
         lambda r: f"The identical file was already processed as {r['invoice_number']}."),
        ("D1 Same IRN", lambda: first(lambda r: r.get("irn") == n["irn"]) if n["irn"] else None, "REJECT_DUPLICATE",
         lambda r: f"The same e-invoice reference (IRN) was already used on {r['invoice_number']}."),
        ("D2 Same number, same year", lambda: first(lambda r: r["vendor_id"] == vid and strict_number(r["invoice_number"]) == strict_number(num)
                                                     and financial_year(r["invoice_date"]) == financial_year(inv_date)) if (vid and num and inv_date) else None,
         "REJECT_DUPLICATE", lambda r: f"Invoice {r['invoice_number']} from this vendor already exists in this financial year."),
        ("D3 Near-identical number, same amount", lambda: first(lambda r: r["vendor_id"] == vid and edit_distance(loose_number(r["invoice_number"]), loose_number(num)) <= 1
                                                                 and abs(r["gross_paise"] - gross) <= 100) if (vid and num and gross is not None) else None,
         "HOLD_DUPLICATE_REVIEW", lambda r: f"{num} looks like a re-send of {r['invoice_number']}: the numbers differ by one character after clean-up and both total {format_inr(r['gross_paise'])}."),
        ("D4 Same amount, close dates", lambda: first(lambda r: r["vendor_id"] == vid and abs(r["gross_paise"] - gross) <= 100
                                                       and _days_between(r["invoice_date"], inv_date) <= window
                                                       and not (r.get("service_period") and n["service_period"] and r["service_period"] != n["service_period"])
                                                       and r.get("po_number") == n["po_number"]) if (vid and gross is not None and inv_date) else None,
         "HOLD_DUPLICATE_REVIEW", lambda r: f"Same amount as {r['invoice_number']}, dated within {window} days, with no different service period to explain it."),
    ]
    for name, find, status, detail in tiers:
        r = None if hit else find()
        if r:
            hit = r
            reasons.append(reason(name.split(" ")[0] + "_DUPLICATE", status, detail(r)))
            checks.append(check(name, "fail", found=r["invoice_number"], note=detail(r)))
        else:
            checks.append(check(name, "info" if hit else "pass", note="Not needed" if hit else None))
    summary = reasons[0]["detail"] if hit else f"Compared against {len(rows)} earlier invoices. No duplicate found."
    return hit, {"stage": "Duplicate check", "status": _stage_status(reasons, checks), "checks": checks, "reasons": reasons, "summary": summary}


# ---------- stage 5: PO match ----------
def match_po(n: dict, ctx: dict):
    checks, reasons, reservation, p = [], [], [], ctx["policy"]

    def finish(summary=None, within=None, over=0):
        status = _stage_status(reasons, checks)
        return {"reservation": reservation, "within_balance_net": within, "over_po_paise": over,
                "result": {"stage": "PO match", "status": status, "checks": checks, "reasons": reasons,
                           "summary": summary if summary is not None else (" ".join(r["detail"] for r in reasons) or "Passed.")}}

    if not n["po_number"]:
        reasons.append(reason("NO_PO_REFERENCE", "REQUEST_INFO", "No PO number appears on this invoice."))
        checks.append(check("PO reference", "fail", found="None"))
        return finish()
    po = next((x for x in ctx["pos"] if x["po_number"] == n["po_number"]), None)
    if not po:
        reasons.append(reason("PO_NOT_FOUND", "REQUEST_INFO", f"PO {n['po_number']} does not exist in the procurement system."))
        checks.append(check("PO exists", "fail", found=n["po_number"]))
        return finish()
    checks.append(check("PO exists", "pass", found=po["po_number"]))
    if n["vendor"] and po["vendor_id"] != n["vendor"]["id"]:
        reasons.append(reason("PO_VENDOR_MISMATCH", "HOLD_VARIANCE", f"PO {po['po_number']} belongs to a different vendor."))
        checks.append(check("PO belongs to this vendor", "fail"))
        return finish()
    checks.append(check("PO belongs to this vendor", "pass"))
    if po["status"] != "open":
        reasons.append(reason("PO_NOT_OPEN", "HOLD_VARIANCE", f"PO {po['po_number']} is {po['status']}."))
        checks.append(check("PO is open", "fail", found=po["status"]))
        return finish()
    po_lines = [l for l in ctx["po_lines"] if l["po_number"] == po["po_number"]]
    prior = [l for r in ctx["ledger"] if r.get("reserves_po") for l in r.get("lines", []) if l["po_number"] == po["po_number"]]

    def billed_net(line_no=None):
        return sum(l["net_paise"] for l in prior if line_no is None or l["line_no"] == line_no)

    def billed_qty(line_no):
        return sum(l.get("qty_milli") or 0 for l in prior if l["line_no"] == line_no)

    def q(m):
        v = Fraction(m, 1000)
        return str(v.numerator) if v.denominator == 1 else str(float(v))

    for i, il in enumerate(n["lines"]):
        scored = sorted(({"l": l, "s": overlap(l["description"], il["description"])} for l in po_lines), key=lambda d: -d["s"])
        if len(po_lines) == 1:
            pl = po_lines[0]
        elif scored and scored[0]["s"] > 0 and (len(scored) < 2 or scored[0]["s"] != scored[1]["s"]):
            pl = scored[0]["l"]
        else:
            pl = None
        tag = f"Line {i + 1}"
        if not pl:
            reasons.append(reason("UNMATCHED_LINE", "HOLD_VARIANCE", f'{tag} ("{il["description"]}") could not be matched to one PO line.'))
            checks.append(check(f"{tag} maps to a PO line", "fail", found=il["description"]))
            continue
        net = il["amount_paise"] or 0
        reservation.append({"po_number": po["po_number"], "line_no": pl["line_no"], "qty_milli": il["qty_milli"], "net_paise": net})
        if pl["kind"] == "goods":
            tol = _tolerance(pl["line_net_paise"], p)
            if il["qty_milli"] is None or il["unit_price_paise"] is None:
                reasons.append(reason("QTY_OR_PRICE_MISSING", "REQUEST_INFO", f"{tag}: goods line has no readable quantity or unit price, so it cannot be checked against the PO."))
                checks.append(check(f"{tag} quantity and price", "fail", found="Missing"))
            if il["unit_price_paise"] is not None and pl["unit_price_paise"] is not None and il["qty_milli"] is not None:
                diff = js_round(Fraction((il["unit_price_paise"] - pl["unit_price_paise"]) * il["qty_milli"], 1000))
                if diff > tol:
                    reasons.append(reason("PRICE_OVER_TOLERANCE", "HOLD_VARIANCE", f"{tag}: price is {format_inr(diff)} above the PO, over the {format_inr(tol)} limit."))
                    checks.append(check(f"{tag} price", "fail", expected=f"{format_inr(pl['unit_price_paise'])} (limit +{format_inr(tol)})", found=format_inr(il["unit_price_paise"])))
                elif diff > 0:
                    reasons.append(reason("PRICE_WITHIN_TOLERANCE", "APPROVE_WITH_NOTE", f"{tag}: price is {format_inr(diff)} above the PO, inside the {format_inr(tol)} limit."))
                    checks.append(check(f"{tag} price", "warn", expected=f"{format_inr(pl['unit_price_paise'])} (limit +{format_inr(tol)})",
                                        found=format_inr(il["unit_price_paise"]), note=f"+{format_inr(diff)} in total"))
                else:
                    checks.append(check(f"{tag} price", "pass", expected=format_inr(pl["unit_price_paise"]), found=format_inr(il["unit_price_paise"])))
            cum = billed_qty(pl["line_no"]) + (il["qty_milli"] or 0)
            received = sum(g["qty_milli"] for g in ctx["grns"] if g["po_number"] == po["po_number"] and g["line_no"] == pl["line_no"])
            if pl["qty_milli"] is not None and cum > pl["qty_milli"]:
                reasons.append(reason("QTY_OVER_ORDERED", "HOLD_VARIANCE", f"{tag}: {q(cum)} billed in total against {q(pl['qty_milli'])} ordered."))
                checks.append(check(f"{tag} quantity vs ordered", "fail", expected=f"up to {q(pl['qty_milli'])}", found=f"{q(cum)} billed in total"))
            else:
                checks.append(check(f"{tag} quantity vs ordered", "pass", expected=f"up to {'n/a' if pl['qty_milli'] is None else q(pl['qty_milli'])}", found=f"{q(cum)} billed in total"))
            if cum > received and (pl["qty_milli"] is None or cum <= pl["qty_milli"]):
                reasons.append(reason("AWAITING_RECEIPT", "HOLD_AWAITING_RECEIPT", f"{tag}: {q(cum)} billed but only {q(received)} received so far."))
                checks.append(check(f"{tag} quantity vs received", "fail", expected=f"up to {q(received)}", found=q(cum)))
            else:
                checks.append(check(f"{tag} quantity vs received", "info" if cum > received else "pass", expected=f"up to {q(received)}", found=q(cum)))
        else:
            prev = billed_net(pl["line_no"])
            cum = prev + net
            tol = _tolerance(pl["line_net_paise"], p)
            checks.append(check(f"{tag} billed so far on this PO line", "info", found=f"{format_inr(prev)} earlier + {format_inr(net)} now = {format_inr(cum)}"))
            if cum > pl["line_net_paise"] + tol:
                reasons.append(reason("CUMULATIVE_OVER_LIMIT", "HOLD_VARIANCE",
                                      f"{tag}: cumulative billing of {format_inr(cum)} passes the limit of {format_inr(pl['line_net_paise'] + tol)} by {format_inr(cum - pl['line_net_paise'] - tol)}."))
                checks.append(check(f"{tag} cumulative vs PO limit", "fail", expected=f"up to {format_inr(pl['line_net_paise'] + tol)}", found=format_inr(cum)))
            elif cum > pl["line_net_paise"]:
                reasons.append(reason("CUMULATIVE_WITHIN_TOLERANCE", "APPROVE_WITH_NOTE", f"{tag}: {format_inr(cum - pl['line_net_paise'])} over the PO line, inside tolerance."))
                checks.append(check(f"{tag} cumulative vs PO limit", "warn", expected=f"up to {format_inr(pl['line_net_paise'] + tol)}", found=format_inr(cum)))
            else:
                checks.append(check(f"{tag} cumulative vs PO limit", "pass", expected=f"up to {format_inr(pl['line_net_paise'] + tol)}", found=format_inr(cum)))
    inv_net = n["net_paise"] or 0
    cum = billed_net() + inv_net
    head_tol = _tolerance(po["net_paise"], p)
    balance = max(0, po["net_paise"] - billed_net())
    over = max(0, cum - po["net_paise"])
    ok = cum <= po["net_paise"] + head_tol
    checks.append(check("Whole PO: total billed", ("warn" if over else "pass") if ok else "fail",
                        expected=f"up to {format_inr(po['net_paise'] + head_tol)}", found=format_inr(cum)))
    if not ok and not any(r["code"] == "CUMULATIVE_OVER_LIMIT" for r in reasons):
        reasons.append(reason("PO_CEILING_EXCEEDED", "HOLD_VARIANCE", f"Total billed {format_inr(cum)} passes the PO limit of {format_inr(po['net_paise'] + head_tol)}."))
    nl = len(n["lines"])
    summary = " ".join(r["detail"] for r in reasons) if reasons else f"Checked {nl} PO line{'' if nl == 1 else 's'}. Price, quantity and PO balance all match."
    return finish(summary, balance, over)


# ---------- stage 6: security (always runs, even after a perfect match) ----------
def security_gate(n: dict, payment_note, ctx: dict):
    checks, reasons = [], []
    if not n["vendor"]:
        near = next((v for v in ctx["vendors"] if overlap(v["name"], n["vendor_name"]) >= 0.6), None) if n["vendor_name"] else None
        if near:
            reasons.append(reason("VENDOR_LOOKALIKE", "BLOCK_FRAUD_REVIEW",
                                  f'"{n["vendor_name"]}" resembles approved vendor "{near["name"]}" but its tax ID {n["vendor_gstin"] or "(none)"} is not on file.'))
            checks.append(check("Tax ID matches master", "fail", expected=near["gstin"], found=n["vendor_gstin"] or "None"))
        else:
            checks.append(check("Vendor identity", "info", note="Vendor is not in the master, so there are no bank details to compare."))
    else:
        v = n["vendor"]
        checks.append(check("Tax ID matches master", "pass", expected=v["gstin"], found=n["vendor_gstin"] or ""))
        if not n["bank_account"] and not n["ifsc"]:
            checks.append(check("Bank details match master", "warn", note="No bank details printed. Payment will use the master."))
        else:
            same = (not n["bank_account"] or n["bank_account"] == _clean(v["bank_account"])) and (not n["ifsc"] or n["ifsc"] == _clean(v["ifsc"]))
            checks.append(check("Bank details match master", "pass" if same else "fail", expected=f"{v['ifsc']} / {v['bank_account']}",
                                found=f"{n['ifsc'] or '?'} / {n['bank_account'] or '?'}"))
            if not same:
                reasons.append(reason("BANK_MISMATCH", "BLOCK_FRAUD_REVIEW",
                                      f"The invoice asks for payment to {n['ifsc']} a/c {n['bank_account']}, but the vendor master has {v['ifsc']} a/c {v['bank_account']}."))
    if payment_note and re.search(r"(bank|banker|account).*(chang|new|updat)|(chang|new|updat).*(bank|banker|account)|urgent|immediately", payment_note, re.I):
        checks.append(check("Payment language", "warn", found=payment_note, note="Logged as a warning. Wording alone never blocks."))
    status = _stage_status(reasons, checks)
    summary = " ".join(r["detail"] for r in reasons) if reasons else "Tax ID and bank details match the vendor master."
    return {"stage": "Security", "status": status, "checks": checks, "reasons": reasons, "summary": summary}


# ---------- stage 7: decide ----------
def _approver_for(gross: int) -> str:
    L = 10000000  # one lakh rupees in paise
    if gross <= 5000000:
        return "Manager"
    if gross <= 5 * L:
        return "Department head"
    if gross <= 50 * L:
        return "Finance head"
    return "CFO"


def decide(n: dict, stages: list, ctx: dict, dup, po):
    reasons = [r for s in stages for r in s["reasons"]]
    gross = n["gross_paise"] or 0
    if (not reasons or all(r["status"] == "APPROVE_WITH_NOTE" for r in reasons)) and gross > ctx["policy"]["auto_ceiling_paise"]:
        reasons.append(reason("OVER_AUTO_CEILING", "ROUTE_APPROVER", f"{format_inr(gross)} is above the {format_inr(ctx['policy']['auto_ceiling_paise'])} auto-approve ceiling."))
    status = sorted(reasons, key=lambda r: STATUS_META[r["status"]]["rank"])[0]["status"] if reasons else "AUTO_APPROVE"
    meta = STATUS_META[status]
    owner, question, payable, disputed = meta["owner"], None, 0, 0
    vendor_name = (n["vendor"] or {}).get("name") or n["vendor_name"] or "the vendor"
    inv_no = n["invoice_number"] or "this invoice"
    if status in ("AUTO_APPROVE", "APPROVE_WITH_NOTE"):
        payable = gross
    if status == "HOLD_VARIANCE":
        net = n["net_paise"] or 0
        if po and po["within_balance_net"] is not None and net > 0 and any(r["code"] in ("CUMULATIVE_OVER_LIMIT", "PO_CEILING_EXCEEDED") for r in reasons):
            payable = js_round(Fraction(gross * min(po["within_balance_net"], net), net))
            disputed = gross - payable
            question = f"Amend PO {n['po_number']} by {format_inr(po['over_po_paise'])}, or dispute the excess with {vendor_name}?"
        else:
            question = f"Should {inv_no} be corrected by {vendor_name}, or should PO {n['po_number']} be amended?"
    if status == "HOLD_DUPLICATE_REVIEW" and dup:
        question = f"Is {inv_no} a revision of {dup['invoice_number']} ({format_inr(dup['gross_paise'])}, status {dup['status']}), or a new bill? Only one of them should be paid."
    if status == "BLOCK_FRAUD_REVIEW":
        r = next(x for x in reasons if x["status"] == "BLOCK_FRAUD_REVIEW")
        if r["code"] == "BANK_MISMATCH" and n["vendor"]:
            question = (f"Call {vendor_name} on {n['vendor']['phone']}, the number in the vendor master and not one on the invoice. "
                        f"Did they really move to {n['ifsc']} a/c ending {(n['bank_account'] or '')[-4:]}?")
        else:
            question = f'Is "{n["vendor_name"]}" really the approved vendor? Confirm with the vendor-master team before anything is paid.'
    if status == "REQUEST_INFO":
        question = "Ask " + vendor_name + " to fix and resend: " + " ".join(r["detail"] for r in reasons if r["status"] == "REQUEST_INFO")
    if status == "HOLD_AWAITING_RECEIPT":
        question = f"Has the remaining quantity on PO {n['po_number']} arrived? This clears once the goods receipt is posted."
    if status == "ROUTE_APPROVER":
        owner = _approver_for(gross)
        question = f"{owner}: approve payment of {format_inr(gross)} to {vendor_name}?"
    worst = " ".join(r["detail"] for r in reasons if r["status"] == status)
    notes = worst if status == "APPROVE_WITH_NOTE" else ""
    ran = [s["stage"].lower() for s in stages if s["status"] != "skipped"]
    if status == "AUTO_APPROVE":
        did = f"Approved {format_inr(payable)} for payment."
    elif status == "APPROVE_WITH_NOTE":
        did = f"Approved {format_inr(payable)} for payment and recorded the note."
    elif status.startswith("REJECT"):
        did = "Rejected. Nothing will be paid."
    elif status == "HOLD_VARIANCE" and payable > 0:
        did = f"Held the invoice. {format_inr(payable)} is within the PO balance and {format_inr(disputed)} is disputed."
    else:
        did = "Stopped before payment. Nothing has been paid."
    who = "No one needs to act." if owner == "None" else f"{owner}. {question or ''}".strip()
    return {
        "status": status, "label": meta["label"], "owner": owner, "reasons": reasons, "question": question,
        "needs_review": meta["review"], "payable_now_paise": payable, "disputed_paise": disputed,
        "policy_version": ctx["policy"]["version"],
        "explanation": {"checked": f"Ran {', '.join(ran)} against policy {ctx['policy']['version']}.",
                        "found": worst or notes or "Every check passed.", "did": did, "who": who},
    }


# ---------- orchestration: `emit` is called with real progress between stages ----------
def process_invoice(x: dict, file_hash, ctx: dict, emit=lambda stage, phase, result=None: None):
    stages = []

    def run(stage, fn):
        emit(stage, "started")
        out = fn()
        result = out["result"]
        stages.append(result)
        emit(stage, "finished", result)
        return out

    v = run("Validate", lambda: dict(zip(("normalized", "result"), validate(x, ctx))))
    n = v["normalized"]
    d = run("Duplicate check", lambda: dict(zip(("duplicate", "result"), check_duplicates(n, file_hash, ctx))))
    dup = d["duplicate"]
    skip_po = bool(dup) or n["net_paise"] is None or not n["lines"]

    def po_stage():
        if not skip_po:
            return match_po(n, ctx)
        note = ("Skipped so a possible duplicate does not use up PO balance or trigger a false over-billing alert." if dup
                else "Skipped because the amounts could not be read.")
        return {"reservation": [], "within_balance_net": None, "over_po_paise": 0,
                "result": {"stage": "PO match", "status": "skipped", "checks": [check("PO match", "info", note=note)], "reasons": [],
                           "summary": "Skipped: possible duplicate, so no PO balance is reserved." if dup else "Skipped: amounts missing."}}

    po = run("PO match", po_stage)
    pn = x.get("fields", {}).get("payment_note") or {}
    pay_note = pn.get("raw_text") if pn.get("status") == "found" else None
    run("Security", lambda: {"result": security_gate(n, pay_note, ctx)})

    emit("Decide", "started")
    decision = decide(n, stages, ctx, dup, None if skip_po else po)
    st = "waiting" if decision["needs_review"] else ("failed" if decision["status"].startswith("REJECT") else "passed")
    result = {"stage": "Decide", "status": st, "summary": f"{decision['label']}. {decision['explanation']['did']}",
              "checks": [check("Final status", "info", found=decision["status"])], "reasons": []}
    stages.append(result)
    emit("Decide", "finished", result)

    ledger_row = None
    if n["vendor"] and n["invoice_number"] and n["invoice_date"] and n["gross_paise"] is not None:
        ledger_row = {
            "vendor_id": n["vendor"]["id"], "invoice_number": n["invoice_number"], "invoice_date": n["invoice_date"],
            "gross_paise": n["gross_paise"], "net_paise": n["net_paise"] or 0, "po_number": n["po_number"], "irn": n["irn"],
            "file_hash": file_hash, "status": decision["status"], "service_period": n["service_period"], "lines": po["reservation"],
            "reserves_po": (not skip_po) and len(po["reservation"]) > 0 and not decision["status"].startswith("REJECT"),
        }
    return {"normalized": n, "stages": stages, "decision": decision, "ledger_row": ledger_row}
