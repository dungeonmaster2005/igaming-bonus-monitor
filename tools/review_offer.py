"""Show a parsed offer next to the phrases each field was read from.

Usage: python -m tools.review_offer OPERATOR_ID PAGES_DIR [--markdown]
PAGES_DIR holds <id>__<page>.html files (as written by tools.fetch_pages).
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from src.config import load_operators
from src.operators import PARSERS

FIELDS = [
    "offer_id", "offer_type", "qualifying_action", "qualifying_amount", "min_deposit", "promo_code",
    "reward_type", "currency", "bonus_percent", "max_bonus", "free_spins", "spin_value",
    "total_reward_value", "eligible_games", "wagering_multiplier", "wagering_applies_to", "max_bet",
    "max_win", "claim_days", "use_days", "expiry_days", "offer_end_date",
    "reward_per_unit", "required_turnover", "over_10x", "terms_hash",
]


def main() -> None:
    op_id, pages_dir = sys.argv[1], Path(sys.argv[2])
    op = next(o for o in load_operators() if o["id"] == op_id)
    pages = {name: (pages_dir / f"{op_id}__{name}.html").read_text(encoding="utf-8") for name in op["pages"]}
    for offer in PARSERS[op["parser"]](pages, op, date.today()):
        row = offer.as_row()
        print(f"| field | value | source phrase / note |\n|---|---|---|")
        for name in FIELDS:
            value = row[name]
            shown = "—" if value is None else (value[:16] + "…" if name == "terms_hash" else value)
            note = offer.evidence.get(name, "")
            print(f"| {name} | {shown} | {note.replace('|', '/')} |")
        print(f"\nterms_text: {len(offer.terms_text)} chars")


if __name__ == "__main__":
    main()
