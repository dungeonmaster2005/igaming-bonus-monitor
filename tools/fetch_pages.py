"""Save operator pages exactly as a GitHub runner sees them (for fixtures and checks).

Usage: python -m tools.fetch_pages OUT_DIR OPERATOR_ID [OPERATOR_ID ...]
Writes OUT_DIR/<id>__<page>.html and OUT_DIR/index.json (status of every page).
Uses the project's PoliteClient: robots.txt, delays, blocks and geo-blocks are never worked around.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from src.config import load_operators
from src.http_client import BlockedError, FetchError, PoliteClient

log = logging.getLogger("fetch_pages")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    out_dir, wanted = Path(sys.argv[1]), set(sys.argv[2:])
    out_dir.mkdir(parents=True, exist_ok=True)
    index = []
    for op in load_operators():
        if op["id"] not in wanted:
            continue
        client = PoliteClient(delay_range=tuple(op["delay_range"]))
        for name, url in op["pages"].items():
            entry = {"operator": op["id"], "page": name, "url": url}
            try:
                html = client.get_html(url)
                path = out_dir / f"{op['id']}__{name}.html"
                path.write_text(html, encoding="utf-8")
                entry.update(result="ok", final_url=client.last_url, file=path.name, chars=len(html))
            except BlockedError as exc:
                entry.update(result="blocked", error=str(exc))
            except FetchError as exc:
                entry.update(result="error", error=str(exc))
            log.info("%s %s: %s", op["id"], name, entry.get("error") or entry["result"])
            index.append(entry)
    (out_dir / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
