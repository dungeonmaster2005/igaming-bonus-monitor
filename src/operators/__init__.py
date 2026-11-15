"""One parser per operator (or per shared site template).

Each parser is ``parse(pages: dict[str, str], op: dict, today: date) -> ParseResult``
where ``pages`` maps the page names from config/operators.yaml to their HTML.
"""

from src.operators import crypto, gamesys, spin_genie, uk_simple, unibet_uk

PARSERS = {
    "unibet_uk": unibet_uk.parse,
    "gamesys": gamesys.parse,
    "mrq": uk_simple.parse_mrq,
    "netbet": uk_simple.parse_netbet,
    "midnite": uk_simple.parse_midnite,
    "spin_genie": spin_genie.parse,
    "bitstarz": crypto.parse_bitstarz,
    "sevenbit": crypto.parse_sevenbit,
    "katsubet": crypto.parse_katsubet,
    "mbit": crypto.parse_mbit,
}
