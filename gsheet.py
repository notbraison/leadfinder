"""Google Sheet version of the lead tracker (used when google_sheet_id is set).

Same columns and rules as the Excel tracker: scan columns are refreshed, your own
columns (Status, Notes, follow-ups...) and any columns you add are kept.
The sheet is read again right before writing, so edits you make while a scan is
running are not lost.
"""
import datetime as dt
import json
import os

import gspread
from gspread.utils import DateTimeOption, ValueRenderOption

import tracker

EPOCH = dt.date(1899, 12, 30)  # Google Sheets / Excel serial date zero
DATE_COLS = ("Next follow-up", "Last contacted", "First seen")


def _serial(d):
    if isinstance(d, dt.datetime):
        return (d - dt.datetime(1899, 12, 30)).total_seconds() / 86400
    return (d - EPOCH).days


def _plain(v):
    if isinstance(v, (dt.date, dt.datetime)):
        return _serial(v)
    return "" if v is None else v


class SheetStore:
    def __init__(self, sheet_id, sa_info=None, sa_file=None):
        if sa_info:
            gc = gspread.service_account_from_dict(sa_info)
        else:
            gc = gspread.service_account(filename=sa_file)
        self.book = gc.open_by_key(sheet_id)
        self.url = self.book.url
        self.leads = self._tab(tracker.SHEET, [c[0] for c in tracker.COLUMNS], fmt=True)
        self.runs = self._tab(tracker.RUNS, tracker.RUN_HEADERS)

    def _tab(self, title, headers, fmt=False):
        try:
            return self.book.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = self.book.add_worksheet(title, rows=2000, cols=max(len(headers), 8))
            ws.update([headers], "A1", value_input_option="RAW")
            if fmt:
                self.format_leads(ws, headers)
            # remove the default empty tab of a brand-new spreadsheet
            for other in self.book.worksheets():
                if other.title in ("Sheet1", "Blad1", "Hoja 1") and len(self.book.worksheets()) > 1:
                    self.book.del_worksheet(other)
            return ws

    # ---------------- reading
    def load(self):
        values = self.leads.get(value_render_option=ValueRenderOption.unformatted,
                                date_time_render_option=DateTimeOption.serial_number)
        if not values:
            return {}, []
        headers = values[0]
        known = {c[0] for c in tracker.COLUMNS}
        extra = [h for h in headers if h and h not in known]
        rows = {}
        for vals in values[1:]:
            row = dict(zip(headers, vals + [""] * (len(headers) - len(vals))))
            dom = str(row.get("Domain") or "").lower()
            key = dom or "noweb:" + str(row.get("Business") or "").lower()
            if key != "noweb:":
                rows[key] = row
        return rows, extra

    def calls_this_month(self):
        month = dt.date.today().strftime("%Y-%m")
        total = 0
        for r in self.runs.get_all_values()[1:]:
            if r and str(r[0]).startswith(month) and len(r) > 5:
                try:
                    total += int(float(r[5] or 0))
                except ValueError:
                    pass
        return total

    def domains(self, status=None, older_than_days=0, limit=None):
        rows, _ = self.load()
        cutoff = (dt.datetime.now() - dt.timedelta(days=older_than_days)).strftime("%Y-%m-%d %H:%M")
        out = []
        for r in rows.values():
            if status and str(r.get("Status") or "") != status:
                continue
            if older_than_days and str(r.get("Last checked") or "") > cutoff:
                continue
            if r.get("Domain"):
                out.append({"website": r["Domain"], "domain": r["Domain"], "checked": str(r.get("Last checked") or "")})
        out.sort(key=lambda x: x["checked"])  # oldest checks first
        return out[:limit] if limit else out

    def count(self):
        return max(len(self.leads.col_values(4)) - 1, 0)

    # ---------------- writing
    def merge_and_save(self, records, note="", google_calls=0, where="local"):
        rows, extra = self.load()  # fresh read right before writing
        headers, ordered, added, updated = tracker.merge(rows, extra, records, _serial(dt.date.today()))
        self._write(headers, ordered)
        self.runs.append_row([dt.datetime.now().strftime("%Y-%m-%d %H:%M"), note, len(records),
                              added, updated, google_calls, where], value_input_option="RAW")
        return self.url, added, updated

    def _write(self, headers, ordered):
        values = [headers] + [[_plain(r.get(h, "")) for h in headers] for r in ordered]
        need_rows = max(len(values) + 50, 2000)
        if self.leads.row_count < need_rows or self.leads.col_count < len(headers):
            self.leads.resize(rows=max(need_rows, self.leads.row_count), cols=max(len(headers), self.leads.col_count))
        self.leads.batch_clear([f"A2:{gspread.utils.rowcol_to_a1(self.leads.row_count, self.leads.col_count)}"])
        self.leads.update(values, "A1", value_input_option="RAW")

    def import_excel(self, path):
        rows, extra = tracker.load(path)
        headers = [c[0] for c in tracker.COLUMNS] + extra
        ordered = sorted(rows.values(), key=lambda r: (-tracker._num(r.get("Score")), str(r.get("Business") or "")))
        self._write(headers, ordered)
        self.runs.append_row([dt.datetime.now().strftime("%Y-%m-%d %H:%M"), f"imported {os.path.basename(path)}",
                              len(ordered), len(ordered), 0, 0, "local"], value_input_option="RAW")
        return len(ordered)

    # ---------------- look and feel (applied once, when the tab is created)
    def format_leads(self, ws=None, headers=None):
        ws = ws or self.leads
        headers = headers or [c[0] for c in tracker.COLUMNS]
        sid = ws.id
        manual = {c[0] for c in tracker.COLUMNS if c[3] == "manual"}
        widths = {c[0]: c[2] for c in tracker.COLUMNS}
        col = {h: i for i, h in enumerate(headers)}
        rgb = lambda h: {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}
        reqs = [
            {"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {
                "frozenRowCount": 1, "frozenColumnCount": 4}}, "fields": "gridProperties(frozenRowCount,frozenColumnCount)"}},
            {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
                            "cell": {"userEnteredFormat": {"backgroundColor": rgb("1F3A5F"), "wrapStrategy": "WRAP",
                                                           "textFormat": {"bold": True, "foregroundColor": rgb("FFFFFF")}}},
                            "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy)"}},
            {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 1},
                            "cell": {"userEnteredFormat": {"verticalAlignment": "TOP"}},
                            "fields": "userEnteredFormat.verticalAlignment"}},
            {"setDataValidation": {"range": {"sheetId": sid, "startRowIndex": 1, "endRowIndex": 10000,
                                             "startColumnIndex": col["Status"], "endColumnIndex": col["Status"] + 1},
                                   "rule": {"condition": {"type": "ONE_OF_LIST", "values": [
                                       {"userEnteredValue": s} for s in tracker.STATUSES]},
                                       "showCustomUi": True, "strict": False}}},
        ]
        for h in manual:
            reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1,
                                                  "startColumnIndex": col[h], "endColumnIndex": col[h] + 1},
                                        "cell": {"userEnteredFormat": {"backgroundColor": rgb("7A5C00")}},
                                        "fields": "userEnteredFormat.backgroundColor"}})
        for h in headers:
            reqs.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS",
                                                                 "startIndex": col[h], "endIndex": col[h] + 1},
                                                       "properties": {"pixelSize": int(widths.get(h, 15) * 7)},
                                                       "fields": "pixelSize"}})
        for h in ("Top findings", "All findings", "Plugins", "Missing headers"):
            reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 1,
                                                  "startColumnIndex": col[h], "endColumnIndex": col[h] + 1},
                                        "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
                                        "fields": "userEnteredFormat.wrapStrategy"}})
        for h in DATE_COLS:
            reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 1,
                                                  "startColumnIndex": col[h], "endColumnIndex": col[h] + 1},
                                        "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}}},
                                        "fields": "userEnteredFormat.numberFormat"}})
        p = col["Priority"]
        for word, color in (("Hot", "F8B4B4"), ("Warm", "FDE68A"), ("Cold", "D1E7DD"), ("Recheck", "D9D9D9")):
            reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
                "ranges": [{"sheetId": sid, "startRowIndex": 1, "startColumnIndex": p, "endColumnIndex": p + 1}],
                "booleanRule": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": word}]},
                                "format": {"backgroundColor": rgb(color)}}}}})
        f = col["Next follow-up"]
        letter = gspread.utils.rowcol_to_a1(1, f + 1).rstrip("1")
        reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
            "ranges": [{"sheetId": sid, "startRowIndex": 1, "startColumnIndex": f, "endColumnIndex": f + 1}],
            "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [
                {"userEnteredValue": f"=AND(ISNUMBER({letter}2),{letter}2<=TODAY())"}]},
                "format": {"textFormat": {"bold": True, "foregroundColor": rgb("C00000")}}}}}})
        reqs.append({"setBasicFilter": {"filter": {"range": {"sheetId": sid}}}})
        self.book.batch_update({"requests": reqs})


def from_config(sheet_id, sa_file="", sa_json=""):
    if not sheet_id:
        return None
    info = json.loads(sa_json) if sa_json else None
    if not info and not (sa_file and os.path.exists(sa_file)):
        raise RuntimeError("google_sheet_id is set but no service account key was found "
                           "(google_service_account_file in config.json, or GOOGLE_SERVICE_ACCOUNT_JSON).")
    return SheetStore(sheet_id, info, sa_file)
