"""The Excel lead tracker.

Scan columns are refreshed on every run. Your own columns (Status, Contact person,
Notes, Last contacted, Next follow-up) and any columns you add yourself are kept.
"""
import datetime as dt
import os

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

# (header, record key, width, kind) - kind: "manual" = yours, never overwritten
COLUMNS = [
    ("Priority", "priority", 9, "auto"),
    ("Score", "score", 7, "auto"),
    ("Business", "business", 28, "auto"),
    ("Domain", "domain", 26, "key"),
    ("Status", None, 12, "manual"),
    ("Next follow-up", None, 13, "manual"),
    ("Top findings", "top_findings", 60, "auto"),
    ("Emails", "emails", 30, "auto"),
    ("Phone (Maps)", "maps_phone", 16, "auto"),
    ("Phones (site)", "site_phones", 16, "auto"),
    ("WhatsApp", "whatsapp", 15, "auto"),
    ("Contact person", None, 18, "manual"),
    ("Last contacted", None, 13, "manual"),
    ("Notes", None, 30, "manual"),
    ("Category", "category", 16, "auto"),
    ("Address", "address", 30, "auto"),
    ("Rating", "rating", 7, "auto"),
    ("Reviews", "reviews", 8, "auto"),
    ("Website", "final_url", 30, "auto"),
    ("Reachable", "reachable", 12, "auto"),
    ("Site state", "site_state", 14, "auto"),
    ("CMS", "cms", 12, "auto"),
    ("Niche", "niche", 22, "auto"),
    ("Authority 0-10", "authority", 9, "auto"),
    ("Global rank", "global_rank", 10, "auto"),
    ("WP version", "wp_version", 10, "auto"),
    ("WP latest", "wp_latest", 9, "auto"),
    ("PHP version", "php_version", 10, "auto"),
    ("Server", "server", 14, "auto"),
    ("HTTPS", "https", 7, "auto"),
    ("SSL valid", "ssl_valid", 8, "auto"),
    ("SSL days left", "ssl_days_left", 9, "auto"),
    ("SSL error", "ssl_error", 22, "auto"),
    ("HTTP->HTTPS", "http_to_https", 9, "auto"),
    ("xmlrpc on", "xmlrpc_enabled", 8, "auto"),
    ("wp-login default", "wp_login_exposed", 9, "auto"),
    ("Missing headers", "missing_headers", 30, "auto"),
    ("Mixed content", "mixed_content", 8, "auto"),
    ("Load time (s)", "response_s", 8, "auto"),
    ("PageSpeed mobile", "pagespeed_mobile", 9, "auto"),
    ("Theme", "theme", 16, "auto"),
    ("Plugins", "plugins", 40, "auto"),
    ("Copyright year", "copyright_year", 9, "auto"),
    ("Socials", "socials", 30, "auto"),
    ("Contact page", "contact_page", 25, "auto"),
    ("Google Maps", "maps_url", 20, "auto"),
    ("All findings", "all_findings", 70, "auto"),
    ("Source", "source", 25, "auto"),
    ("First seen", None, 11, "first_seen"),
    ("Last checked", "checked_at", 15, "auto"),
]
STATUSES = ["New", "Contacted", "Replied", "Meeting", "Quoted", "Won", "Lost", "Not a fit"]
SHEET, RUNS = "Leads", "Runs"
RUN_HEADERS = ["When", "What", "Leads found", "New", "Updated", "Google calls", "Where"]
SKIP_BLANK_OVERWRITE = ("business", "maps_phone", "address", "rating", "reviews", "maps_url", "category", "source")


def _cell(v):
    if isinstance(v, bool):
        return "Yes" if v else "No"
    return v


def _row_key(rec):
    return (rec.get("domain") or "").lower() or "noweb:" + (rec.get("business") or "").lower()


def load(path):
    """Return ({key: {header: value}}, extra_headers) from an existing workbook."""
    if not os.path.exists(path):
        return {}, []
    wb = load_workbook(path)
    if SHEET not in wb.sheetnames:
        return {}, []
    ws = wb[SHEET]
    headers = [c.value for c in ws[1]]
    known = {c[0] for c in COLUMNS}
    extra = [h for h in headers if h and h not in known]
    rows = {}
    for vals in ws.iter_rows(min_row=2, values_only=True):
        row = dict(zip(headers, vals))
        dom = (row.get("Domain") or "").lower()
        key = dom or "noweb:" + (row.get("Business") or "").lower()
        if key != "noweb:":
            rows[key] = row
    return rows, extra


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def merge(rows, extra, records, today):
    """Merge scan records into existing rows. Returns (headers, ordered rows, added, updated)."""
    added = updated = 0
    for rec in records:
        key = _row_key(rec)
        if key == "noweb:":
            continue
        old = rows.get(key)
        new = dict(old) if old else {}
        for header, k, _, kind in COLUMNS:
            if kind in ("auto", "key"):
                v = _cell(rec.get(k, ""))
                if header == "Website" and not v:
                    v = rec.get("website", "")
                # don't wipe info we had (e.g. Maps phone) with blanks from a plain rescan
                if v in ("", None) and old and old.get(header) not in ("", None) and k in SKIP_BLANK_OVERWRITE:
                    continue
                new[header] = v
        if old:
            updated += 1
        else:
            added += 1
            new["Status"] = "New"
            new["First seen"] = today
        if not rec.get("domain"):
            new["Reachable"] = new.get("Reachable") or "No website listed"
            new["Top findings"] = new.get("Top findings") or "- No website listed on Google Maps (possible new-site lead)"
        rows[key] = new

    headers = [c[0] for c in COLUMNS] + extra
    ordered = sorted(rows.values(), key=lambda r: (-_num(r.get("Score")), str(r.get("Business") or "")))
    return headers, ordered, added, updated


def merge_and_save(path, records, run_note="", google_calls=0, where="local"):
    rows, extra = load(path)
    headers, ordered, added, updated = merge(rows, extra, records, dt.date.today())

    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    ws.append(headers)
    for r in ordered:
        ws.append([r.get(h, "") for h in headers])
    _format(ws, headers, len(ordered))

    # Runs log (kept between runs)
    log = wb.create_sheet(RUNS)
    log.append(RUN_HEADERS)
    if os.path.exists(path):
        try:
            old_wb = load_workbook(path, read_only=True)
            if RUNS in old_wb.sheetnames:
                for vals in list(old_wb[RUNS].iter_rows(min_row=2, values_only=True)):
                    log.append(list(vals))
        except Exception:
            pass
    log.append([dt.datetime.now().strftime("%Y-%m-%d %H:%M"), run_note, len(records), added, updated,
                google_calls, where])
    log.column_dimensions["A"].width = 17
    log.column_dimensions["B"].width = 50
    for c in log[1]:
        c.font = Font(bold=True)

    try:
        wb.save(path)
        saved = path
    except PermissionError:
        base, ext = os.path.splitext(path)
        saved = f"{base}-{dt.datetime.now():%Y%m%d-%H%M%S}{ext}"
        wb.save(saved)
        print(f"\n! {path} is open in Excel, so results were saved to {saved}")
    return saved, added, updated


def _format(ws, headers, n):
    head_fill = PatternFill("solid", fgColor="1F3A5F")
    manual_fill = PatternFill("solid", fgColor="7A5C00")
    manual = {c[0] for c in COLUMNS if c[3] == "manual"}
    widths = {c[0]: c[2] for c in COLUMNS}
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = manual_fill if h in manual else head_fill
        c.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[get_column_letter(i)].width = widths.get(h, 15)
    ws.row_dimensions[1].height = 32
    wrap_cols = {headers.index(h) + 1 for h in ("Top findings", "All findings", "Plugins", "Missing headers") if h in headers}
    for row in ws.iter_rows(min_row=2, max_row=n + 1):
        for c in row:
            c.alignment = Alignment(wrap_text=c.column in wrap_cols, vertical="top")
    ws.freeze_panes = "E2"
    last = get_column_letter(len(headers))
    ws.auto_filter.ref = f"A1:{last}{max(n + 1, 2)}"

    # Priority colours
    col = get_column_letter(headers.index("Priority") + 1)
    rng = f"{col}2:{col}{max(n + 1, 2)}"
    for word, color in (("Hot", "F8B4B4"), ("Warm", "FDE68A"), ("Cold", "D1E7DD"), ("Recheck", "D9D9D9")):
        ws.conditional_formatting.add(rng, FormulaRule(formula=[f'{col}2="{word}"'], fill=PatternFill("solid", fgColor=color)))

    # Status dropdown
    scol = get_column_letter(headers.index("Status") + 1)
    dv = DataValidation(type="list", formula1='"' + ",".join(STATUSES) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"{scol}2:{scol}5000")
    # Follow-up dates overdue -> red text
    fcol = get_column_letter(headers.index("Next follow-up") + 1)
    ws.conditional_formatting.add(f"{fcol}2:{fcol}5000", FormulaRule(
        formula=[f'AND(ISNUMBER({fcol}2),{fcol}2<=TODAY())'], font=Font(bold=True, color="C00000")))
    for c in ws[fcol][1:] + ws[get_column_letter(headers.index("Last contacted") + 1)][1:] + \
            ws[get_column_letter(headers.index("First seen") + 1)][1:]:
        c.number_format = "yyyy-mm-dd"


def domains(path, status=None):
    rows, _ = load(path)
    out = []
    for r in rows.values():
        if status and (r.get("Status") or "") != status:
            continue
        if r.get("Domain"):
            out.append({"website": r["Domain"], "domain": r["Domain"]})
    return out
