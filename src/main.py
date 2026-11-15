"""Daily run: read every enabled operator, update the state cache and the Google Sheet.

Usage:
  python -m src.main                         fetch pages live (CI, GitHub-hosted runner)
  python -m src.main --pages-dir DIR --date YYYY-MM-DD
                                             parse pages saved by tools/fetch_pages.py
                                             (DIR/<operator>__<page>.html) as of that date
  --no-sheets                                skip the Google Sheet (state is still saved)
  --no-telegram                              log the alert instead of sending it
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from src import alerts, sheets, tables
from src.config import load_alert_config, load_operators
from src.http_client import BlockedError, FetchError, PoliteClient
from src.operators import PARSERS
from src.operators.base import LayoutChanged
from src.state import OperatorRun, State

log = logging.getLogger("monitor")

# Where the pages are read from. GitHub-hosted runners egress from the US (phase 0: Virginia,
# AS8075); the sheet says so, because operators show different terms by location.
COLLECTED_FROM = os.getenv("COLLECTED_FROM", "a US IP")


def read_operator(op: dict, today: date, pages_dir: Path | None) -> OperatorRun:
    run = OperatorRun(op["id"], op["operator"], op["market"], 0, len(op["pages"]))
    pages: dict[str, str] = {}
    client = None if pages_dir else PoliteClient(delay_range=tuple(op["delay_range"]))
    for name, url in op["pages"].items():
        try:
            if pages_dir:
                path = pages_dir / f"{op['id']}__{name}.html"
                if not path.exists():
                    raise FetchError(f"{path.name} not saved")
                pages[name] = path.read_text(encoding="utf-8")
            else:
                pages[name] = client.get_html(url)
            run.pages_ok += 1
        except BlockedError as exc:
            run.problems.append(f"{name}: blocked — {exc}")
        except FetchError as exc:
            run.problems.append(f"{name}: {exc}")
    if run.problems:
        return run  # never parse a partial set of pages: missing text would read as changed terms
    try:
        result = PARSERS[op["parser"]](pages, op, today)
    except LayoutChanged as exc:
        run.problems.append(f"layout changed: {exc}")
        return run
    except Exception as exc:  # one parser never breaks the others
        log.exception("%s: parser failed", op["id"])
        run.problems.append(f"parser error: {exc!r}")
        return run
    run.offers, run.issues = result.offers, result.issues
    log.info("%s: %d offer rows, %d issues", op["id"], len(run.offers), len(run.issues))
    return run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages-dir", type=Path)
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--no-sheets", action="store_true")
    parser.add_argument("--no-telegram", action="store_true", help="log the message instead of sending")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()
    load_dotenv()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(levelname)s %(name)s: %(message)s")
    today = args.date or date.today()

    operators = load_operators()
    enabled = [op for op in operators if op.get("enabled")]
    disabled = [op for op in operators if not op.get("enabled")]
    runs = [read_operator(op, today, args.pages_dir) for op in enabled]

    alert_config = load_alert_config()
    state = State(args.data_dir)
    state.configure(alert_config)
    result = state.apply(runs, today)
    ending = [] if result.events.first_run else state.offers_ending(result, int(alert_config["offer_ending_days"]))
    log.info("%d offer rows, %d issues, %d changes", len(result.rows), len(result.issues), len(result.changes))

    sheet_url = None
    if args.no_sheets or not sheets.sheets_configured():
        if not args.no_sheets:
            log.warning("Google Sheets not configured (SHEET_ID + GOOGLE_CREDS/GOOGLE_CREDS_PATH) — skipped")
    else:
        sheet_url = write_sheets(state, result, enabled, disabled)

    message = alerts.build_message(result, alert_config, ending, sheet_url)
    if result.events.first_run:
        log.info("First run: baseline recorded, no alerts")
    elif message is None:
        log.info("No changes: no message")
    elif args.no_telegram or not alerts.telegram_configured():
        log.info("Telegram skipped; message would be:\n%s", message)
    else:
        alerts.send_telegram(message)
    # saved last: if the sheet or Telegram failed, the next run diffs against the same baseline again
    state.save()


def write_sheets(state: State, result, enabled: list[dict], disabled: list[dict]) -> str:
    issues = [i for i, _, _ in result.issues]
    enabled_ids = {op["id"] for op in enabled}
    sheet_tables = [
        tables.uk_table(result, issues), tables.crypto_table(result, issues), tables.issues_table(result),
        tables.changes_table(state.changes), tables.terms_table(result, state.terms),
        tables.status_table({k: v for k, v in state.status.items() if k in enabled_ids}, disabled,
                            state.noise),
    ]
    disclaimer = tables.DISCLAIMER.format(where=COLLECTED_FROM, day=result.day)
    book = sheets.open_spreadsheet()
    sheets.SheetsWriter(book, disclaimer).write_all(sheet_tables, tables.history_rows(result), result.day)
    return book.url

if __name__ == "__main__":
    main()
