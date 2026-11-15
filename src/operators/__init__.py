"""One parser module per operator (or per shared site template).

Each module exposes ``parse(pages: dict[str, str], op: dict, today: date) -> list[Offer]``
where ``pages`` maps the page names from config/operators.yaml to their HTML.
"""

from src.operators import unibet_uk

PARSERS = {
    "unibet_uk": unibet_uk.parse,
}
