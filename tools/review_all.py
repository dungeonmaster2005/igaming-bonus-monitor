"""Parse every enabled operator from saved pages and print offers, empty fields and issues.

Usage: python -m tools.review_all PAGES_DIR [--json OUT.json]
"""

from __future__ import annotations

import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from src.config import load_operators
from src.models import PARSED_FIELDS
from src.operators import PARSERS

from src.tables import NOT_APPLICABLE  # noqa: E402  (one definition for sheet and review)


def load(pages_dir: Path, op: dict) -> dict[str, str]:
    pages = {}
    for name in op["pages"]:
        path = pages_dir / f"{op['id']}__{name}.html"
        if path.exists():
            pages[name] = path.read_text(encoding="utf-8")
    return pages


def empty_reason(offer, name, issues_by_field) -> str:
    if name in issues_by_field:
        return issues_by_field[name]
    if name in offer.stated_none:
        return "stated as none"
    if name in offer.field_notes:
        return offer.field_notes[name]
    if name in NOT_APPLICABLE.get(offer.reward_type, set()):
        return "n/a for " + offer.reward_type
    if name == "wagering_applies_to" and offer.wagering_multiplier == 0:
        return "n/a (no wagering)"
    if name == "total_reward_value":
        return "parts missing (spin value or bonus)"
    return "not stated on the fetched pages"


def main() -> None:
    pages_dir = Path(sys.argv[1])
    offers, issues, failures = [], [], []
    for op in load_operators():
        if not op.get("enabled"):
            continue
        try:
            result = PARSERS[op["parser"]](load(pages_dir, op), op, date(2026, 11, 15))
        except Exception as exc:  # report and continue: one parser never breaks the others
            failures.append((op["id"], repr(exc)))
            continue
        offers += result.offers
        issues += result.issues
    out = {"offers": [], "empty": [], "issues": [], "failures": failures}
    for o in offers:
        row = o.as_row()
        out["offers"].append({k: (str(v) if isinstance(v, (Decimal, date)) else v) for k, v in row.items()
                              if k not in ("terms_text",)})
        by_field = {i.field: f"{i.type}: {i.detail}" for i in issues if i.offer_id == o.offer_id}
        for name in PARSED_FIELDS:
            if getattr(o, name) is None:
                out["empty"].append([o.operator, o.offer_label or "", name, empty_reason(o, name, by_field)])
    for i in issues:
        out["issues"].append({"operator": i.operator, "offer_id": i.offer_id, "field": i.field, "type": i.type,
                              "detail": i.detail, "quotes": [[q.source_url, q.text] for q in i.quotes]})
    text = json.dumps(out, ensure_ascii=False, indent=1)
    if "--json" in sys.argv:
        Path(sys.argv[sys.argv.index("--json") + 1]).write_text(text, encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
