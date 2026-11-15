"""Alert test on an artificial "yesterday" (state in a throw-away directory, never data/).

Day 1 (2026-11-14): the saved CI pages as they are -> baseline, no message.
Day 2 (2026-11-15): the same pages with three edits made on purpose:
  * Unibet: wagering 10x -> 20x (offer page and lobby banner)
  * NetBet: two clauses of the terms reworded
  * Spin Genie: choice C removed
The message is printed; with --send it is also sent to Telegram, headed as a test.

Usage: python -m tools.alert_demo WORK_DIR [--send]
"""

from __future__ import annotations

import logging
import re
import shutil
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from src import alerts
from src.config import load_alert_config, load_operators
from src.main import read_operator
from src.state import State

FIXTURES = Path("tests/fixtures")
EDITS = {
    "unibet_uk__offer.html": [(r"\bx10\b", "x20"), (r"\b10x\b", "20x")],
    "unibet_uk__lobby.html": [(r"\b10x\b", "20x")],
    "netbet_uk__offer.html": [
        ("1 bonus per household/computer/IP address", "1 bonus per person, household or device"),
        ("Matchedbettingblog or Teamprofit", "Matchedbettingblog, Teamprofit or Profit Accumulator"),
    ],
}
TEST_NOTE = ("TEST on an artificial \"yesterday\": the changes below were made on purpose to the saved "
             "pages; they were not observed on the operators' sites.")


def runs_from(pages_dir: Path, day: date):
    return [read_operator(op, day, pages_dir) for op in load_operators() if op.get("enabled")]


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    load_dotenv()
    work = Path(sys.argv[1])
    shutil.rmtree(work, ignore_errors=True)
    state_dir, pages_dir = work / "state", work / "pages_day2"
    config = load_alert_config()

    # day 1: baseline
    state = State(state_dir)
    day1 = state.apply(runs_from(FIXTURES, date(2026, 11, 14)), date(2026, 11, 14))
    assert alerts.build_message(day1, config, []) is None, "first run must not alert"
    state.save()
    print("Day 1 (baseline): no message —", len(day1.rows), "offer rows recorded")

    # day 2: edited copy of the pages
    shutil.copytree(FIXTURES, pages_dir)
    for name, edits in EDITS.items():
        path = pages_dir / name
        html = path.read_text(encoding="utf-8")
        for pattern, new in edits:
            html, n = re.subn(pattern if pattern.startswith("\\b") else re.escape(pattern), new, html)
            assert n, f"{name}: {pattern!r} not found"
        path.write_text(html, encoding="utf-8")
    runs = runs_from(pages_dir, date(2026, 11, 15))
    spin = next(r for r in runs if r.operator_id == "spin_genie")
    spin.offers = [o for o in spin.offers if not (o.offer_label or "").startswith("Choice C")]

    state = State(state_dir)
    state.configure(config)
    day2 = state.apply(runs, date(2026, 11, 15))
    ending = state.offers_ending(day2, int(config["offer_ending_days"]))
    message = alerts.build_message(day2, config, ending, heading_note=TEST_NOTE)
    print("\nDay 2 message:\n")
    print(message)
    if "--send" in sys.argv:
        alerts.send_telegram(message)
        print("\nSent to Telegram.")


if __name__ == "__main__":
    main()
