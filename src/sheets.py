"""Google Sheets output (adapted from eu-price-monitor's sheets.py).

Credentials come from the environment only:
  GOOGLE_CREDS       service-account JSON as a string (CI)
  GOOGLE_CREDS_PATH  path to the service-account JSON file (local)
  SHEET_ID           spreadsheet id (the part of the URL after /d/)

Layout of every sheet: row 1 the disclaimer, row 2 what the sheet shows, row 3 the
header, data from row 4. Sheets are rewritten in place (their gid, and so bookmarks
and in-sheet links, stay stable); "history" keeps one snapshot per day.
"""

from __future__ import annotations

import json
import logging
import os
import re

import gspread

from src import tables
from src.tables import Cell, Table

log = logging.getLogger(__name__)

SHEET_LOCALE = "en_GB"         # "," separates function arguments, "." is the decimal point
SHEET_TIMEZONE = "Etc/UTC"
DEFAULT_TAB_NAMES = {"Sheet1", "Лист1", "Arkusz1", "Tabellenblatt1", "Blad1", "List 1", "Feuille 1"}
TAB_ORDER = ["offers_uk", "offers_crypto", "issues", "changes", "terms", "status", "history"]
DISCLAIMER_ROW, LEGEND_ROW, HEADER_ROW, FIRST_DATA_ROW = 1, 2, 3, 4   # 1-based
LEGEND_WIDTH = 1500  # px

HEADER_BG = {"red": 0.16, "green": 0.22, "blue": 0.33}
NOTE_FG = {"red": 0.35, "green": 0.35, "blue": 0.35}
LINK_FG = {"red": 0.07, "green": 0.33, "blue": 0.80}
GREY_FG = {"red": 0.55, "green": 0.55, "blue": 0.55}
GREEN_BG = {"red": 0.80, "green": 0.92, "blue": 0.80}
RED_BG = {"red": 0.96, "green": 0.80, "blue": 0.80}
AMBER_BG = {"red": 1.0, "green": 0.90, "blue": 0.70}
YELLOW_BG = {"red": 1.0, "green": 0.97, "blue": 0.80}
GREY_BG = {"red": 0.92, "green": 0.92, "blue": 0.92}
RED_FG = {"red": 0.70, "green": 0.10, "blue": 0.10}

# Empty states are told apart by text and by a quiet style; conflicts and issues stand out.
STYLES = {
    "none": {"textFormat": {"italic": True}, "horizontalAlignment": "CENTER"},
    "na": {"textFormat": {"foregroundColor": GREY_FG}, "horizontalAlignment": "CENTER"},
    "missing": {"textFormat": {"foregroundColor": GREY_FG}, "horizontalAlignment": "CENTER"},
    "conflict": {"textFormat": {"bold": True, "foregroundColor": RED_FG}, "backgroundColor": RED_BG,
                 "horizontalAlignment": "CENTER"},
    "issue": {"backgroundColor": YELLOW_BG},
    "sev_high": {"backgroundColor": RED_BG, "textFormat": {"bold": True}},
    "sev_medium": {"backgroundColor": AMBER_BG},
    "sev_low": {"backgroundColor": YELLOW_BG},
}
STATUS_COLOURS = {"OK": GREEN_BG, "STALE": RED_BG, "DISABLED": GREY_BG}


def sheets_configured() -> bool:
    return bool(os.getenv("SHEET_ID") and (os.getenv("GOOGLE_CREDS") or os.getenv("GOOGLE_CREDS_PATH")))


def open_spreadsheet() -> gspread.Spreadsheet:
    if creds_json := os.getenv("GOOGLE_CREDS"):
        client = gspread.service_account_from_dict(json.loads(creds_json))
    else:
        client = gspread.service_account(filename=os.environ["GOOGLE_CREDS_PATH"])
    return client.open_by_key(os.environ["SHEET_ID"])


class SheetsWriter:
    def __init__(self, book: gspread.Spreadsheet, disclaimer: str) -> None:
        self.book = book
        self.disclaimer = disclaimer
        self.book.batch_update({"requests": [{"updateSpreadsheetProperties": {
            "properties": {"locale": SHEET_LOCALE, "timeZone": SHEET_TIMEZONE}, "fields": "locale,timeZone"}}]})

    def write_all(self, sheet_tables: list[Table], history_rows: list[list], day: str) -> None:
        sheets = {t.title: self._worksheet(t.title, len(t.columns)) for t in sheet_tables}
        history = self._worksheet("history", len(tables.HISTORY_HEADERS))
        # in-sheet links ("#issue:<id>") resolve to the row the anchor lands on
        anchors = {key: (sheets[t.title].id, FIRST_DATA_ROW + index)
                   for t in sheet_tables for key, index in t.anchors.items()}
        for t in sheet_tables:
            self._write_table(sheets[t.title], t, anchors)
        self._write_history(history, history_rows, day)
        self._drop_default_tab()
        self._order_tabs()
        log.info("Google Sheet updated: %s", self.book.url)

    # -- sheets -------------------------------------------------------------

    def _worksheet(self, title: str, n_cols: int) -> gspread.Worksheet:
        try:
            return self.book.worksheet(title)
        except gspread.WorksheetNotFound:
            return self.book.add_worksheet(title, rows=50, cols=n_cols)

    def _write_table(self, ws: gspread.Worksheet, t: Table, anchors: dict) -> None:
        n_cols, n_rows = len(t.columns), len(t.rows)
        header_cells = [{"userEnteredValue": {"stringValue": c.header}} for c in t.columns]
        grid = [
            [{"userEnteredValue": {"stringValue": self.disclaimer}}],
            [{"userEnteredValue": {"stringValue": t.legend}}],
            header_cells,
            *[[self._cell(c, anchors) for c in row] for row in t.rows],
        ]
        last = FIRST_DATA_ROW - 1 + n_rows
        requests = self._reset(ws, n_cols, max(last, FIRST_DATA_ROW) + 2)
        requests.append({"updateCells": {"rows": [{"values": r} for r in grid], "fields": "*",
                                         "start": {"sheetId": ws.id, "rowIndex": 0, "columnIndex": 0}}})
        requests += self._layout(ws.id, t, last)
        if t.status_column and n_rows:
            i = [c.header for c in t.columns].index(t.status_column)
            rng = _rng(ws.id, HEADER_ROW, last, i, i + 1)
            requests += [_rule(rng, {"type": "TEXT_STARTS_WITH", "values": [{"userEnteredValue": word}]},
                               {"backgroundColor": bg}) for word, bg in STATUS_COLOURS.items()]
        self.book.batch_update({"requests": requests})

    def _write_history(self, ws: gspread.Worksheet, new_rows: list[list], day: str) -> None:
        """Keep one snapshot per day: drop rows already written for `day`, append today's."""
        headers = tables.HISTORY_HEADERS
        values = ws.get_all_values()
        existing = values[FIRST_DATA_ROW - 1:] if len(values) >= HEADER_ROW else []
        if len(values) >= HEADER_ROW and values[HEADER_ROW - 1][:len(headers)] != headers and existing:
            current = values[HEADER_ROW - 1]
            if headers[:len(current)] != current:
                raise RuntimeError("history sheet has data with different columns — migrate it manually")
        kept = [r for r in existing if r and r[0] != day and any(r)]
        rows = kept + new_rows
        rows.sort(key=lambda r: r[0], reverse=True)  # newest first
        n_cols = len(headers)
        grid = [[self.disclaimer], [tables.HISTORY_LEGEND], headers, *[r + [""] * (n_cols - len(r)) for r in rows]]
        last = FIRST_DATA_ROW - 1 + len(rows)
        requests = self._reset(ws, n_cols, last + 2)
        requests.append({"updateCells": {
            "rows": [{"values": [_raw(v) for v in r]} for r in grid],
            "fields": "userEnteredValue", "start": {"sheetId": ws.id, "rowIndex": 0, "columnIndex": 0}}})
        cols = [tables.Column(h, 150 if h in ("row_key", "offer", "terms_hash", "eligible_games") else 95)
                for h in headers]
        requests += self._layout(ws.id, Table("history", cols, [[]] * len(rows), tables.HISTORY_LEGEND), last)
        self.book.batch_update({"requests": requests})

    def _reset(self, ws: gspread.Worksheet, n_cols: int, n_rows: int) -> list[dict]:
        """Requests that wipe a sheet (values, formats, notes, validation, filters, rules) and size it."""
        meta = self.book.fetch_sheet_metadata(
            {"fields": "sheets(properties(sheetId,gridProperties),conditionalFormats,basicFilter)"})
        sheet = next(s for s in meta["sheets"] if s["properties"]["sheetId"] == ws.id)
        requests: list[dict] = [{"deleteConditionalFormatRule": {"sheetId": ws.id, "index": 0}}
                                for _ in sheet.get("conditionalFormats", [])]
        if "basicFilter" in sheet:
            requests.append({"clearBasicFilter": {"sheetId": ws.id}})
        requests += [
            {"unmergeCells": {"range": {"sheetId": ws.id}}},
            {"updateCells": {"range": {"sheetId": ws.id}, "fields": "*"}},
        ]
        requests.append({"updateSheetProperties": {"properties": {"sheetId": ws.id, "gridProperties": {
            "rowCount": n_rows, "columnCount": n_cols, "frozenRowCount": HEADER_ROW}},
            "fields": "gridProperties(rowCount,columnCount,frozenRowCount)"}})
        return requests

    @staticmethod
    def _layout(sheet_id: int, t: Table, last_row: int) -> list[dict]:
        n_cols = len(t.columns)
        requests: list[dict] = []
        # the disclaimer spans the sheet; the legend wraps inside the first screen width
        legend_cols, width = 0, 0
        for col in t.columns:
            if legend_cols and width + col.width > LEGEND_WIDTH:
                break
            legend_cols, width = legend_cols + 1, width + col.width
        requests.append({"mergeCells": {"mergeType": "MERGE_ALL", "range": _rng(sheet_id, 0, 1, 0, n_cols)}})
        if legend_cols > 1:
            requests.append({"mergeCells": {"mergeType": "MERGE_ALL", "range": _rng(sheet_id, 1, 2, 0, legend_cols)}})
        requests += [
            {"repeatCell": {"range": _rng(sheet_id, 0, 1, 0, n_cols), "cell": {"userEnteredFormat": {
                "textFormat": {"bold": True}, "backgroundColor": AMBER_BG}},
                "fields": "userEnteredFormat(textFormat,backgroundColor)"}},
            {"repeatCell": {"range": _rng(sheet_id, 1, 2, 0, n_cols), "cell": {"userEnteredFormat": {
                "textFormat": {"italic": True, "foregroundColor": NOTE_FG}, "wrapStrategy": "WRAP",
                "verticalAlignment": "TOP"}},
                "fields": "userEnteredFormat(textFormat,wrapStrategy,verticalAlignment)"}},
            {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 1,
                                                     "endIndex": 2}, "properties": {"pixelSize": 64},
                                           "fields": "pixelSize"}},
            {"repeatCell": {"range": _rng(sheet_id, 2, 3, 0, n_cols), "cell": {"userEnteredFormat": {
                "backgroundColor": HEADER_BG, "wrapStrategy": "WRAP", "verticalAlignment": "MIDDLE",
                "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}}},
                "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy,verticalAlignment)"}},
            {"setBasicFilter": {"filter": {"range": _rng(sheet_id, HEADER_ROW - 1, max(last_row, HEADER_ROW),
                                                         0, n_cols)}}},
        ]
        for i, col in enumerate(t.columns):
            requests.append({"updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                "properties": {"pixelSize": col.width}, "fields": "pixelSize"}})
        return requests

    @staticmethod
    def _cell(c: Cell, anchors: dict) -> dict:
        data: dict = {}
        fmt: dict = {"verticalAlignment": "TOP"}
        value = c.value
        link = c.link
        if link and link.startswith("#"):
            target = anchors.get(link)
            link = f"#gid={target[0]}&range=A{target[1]}" if target else None
        if link:
            data["userEnteredValue"] = {"formulaValue": f'=HYPERLINK("{_q(link)}",{_formula_literal(value)})'}
            fmt["textFormat"] = {"foregroundColor": LINK_FG, "underline": True}
        elif isinstance(value, bool):
            data["userEnteredValue"] = {"boolValue": value}
        elif isinstance(value, (int, float)) or hasattr(value, "is_finite"):
            data["userEnteredValue"] = {"numberValue": float(value)}
        else:
            data["userEnteredValue"] = {"stringValue": "" if value is None else str(value)}
        if c.checkbox:
            data["dataValidation"] = {"condition": {"type": "BOOLEAN"}}
            fmt["horizontalAlignment"] = "CENTER"
        if c.fmt:
            fmt["numberFormat"] = {"type": "NUMBER", "pattern": c.fmt}
            fmt.setdefault("horizontalAlignment", "RIGHT")
        if c.style:
            style = STYLES[c.style]
            for k, v in style.items():
                fmt[k] = {**fmt.get(k, {}), **v} if isinstance(v, dict) and k == "textFormat" else v
        fmt["wrapStrategy"] = "WRAP" if c.wrap else "CLIP"  # long cells stay one line; the note has it all
        data["userEnteredFormat"] = fmt
        if c.note:
            data["note"] = c.note
        return data

    def _drop_default_tab(self) -> None:
        for ws in self.book.worksheets():
            if ws.title in DEFAULT_TAB_NAMES and len(self.book.worksheets()) > 1 and \
                    not any(cell for row in ws.get_all_values() for cell in row):
                self.book.del_worksheet(ws)

    def _order_tabs(self) -> None:
        sheets = {ws.title: ws for ws in self.book.worksheets()}
        ordered = [sheets[t] for t in TAB_ORDER if t in sheets] + \
                  [ws for t, ws in sheets.items() if t not in TAB_ORDER]
        self.book.reorder_worksheets(ordered)


# --- helpers ------------------------------------------------------------------------

_NUMBER = re.compile(r"-?\d+(\.\d+)?")


def _raw(value) -> dict:
    """History cell: numbers as numbers, everything else as text (never parsed as a formula)."""
    text = str(value)
    if _NUMBER.fullmatch(text):
        return {"userEnteredValue": {"numberValue": float(text)}}
    return {"userEnteredValue": {"stringValue": text}}


def _q(text: str) -> str:
    return text.replace('"', '""')


def _formula_literal(value) -> str:
    """The display argument of HYPERLINK: numbers stay numbers (formats and sorting keep working)."""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)) or hasattr(value, "is_finite"):
        return f"{value:f}" if hasattr(value, "is_finite") else str(value)
    text = "" if value is None else str(value)
    return f'"{_q(text[:2000])}"'


def _rng(sheet_id: int, r0: int, r1: int, c0: int, c1: int) -> dict:
    return {"sheetId": sheet_id, "startRowIndex": r0, "endRowIndex": r1,
            "startColumnIndex": c0, "endColumnIndex": c1}


def _rule(rng: dict, condition: dict, fmt: dict) -> dict:
    return {"addConditionalFormatRule": {"index": 0, "rule": {
        "ranges": [rng], "booleanRule": {"condition": condition, "format": fmt}}}}
