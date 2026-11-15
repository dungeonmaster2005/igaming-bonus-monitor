"""Strip scripts, styles and SVG from saved pages (parsers only read visible text).

Usage: python -m tools.shrink_fixture FILE [FILE ...]   (rewrites the files in place)
"""

import sys
from pathlib import Path

from bs4 import BeautifulSoup


def main() -> None:
    for name in sys.argv[1:]:
        path = Path(name)
        page = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
        for tag in page(["script", "style", "svg", "noscript", "link", "iframe"]):
            tag.decompose()
        path.write_text(str(page), encoding="utf-8")


if __name__ == "__main__":
    main()
