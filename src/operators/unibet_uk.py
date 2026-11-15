"""Unibet UK casino welcome offer.

Offer page (server-rendered; CSS class names are hashed, so only ids are used):
  <h1>                 headline
  #expandable-text     offer-specific "Full Terms and Conditions"
  #terms               general Promotional Terms (the max-bet rule lives here)
Casino lobby: the promo banner restates the offer ("300 x £0.10 Free Spins ...").
"""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import parse_qs, urlsplit

from src.models import PARSED_FIELDS, ParseResult
from src.operators.base import LayoutChanged, block_segments, new_offer, soup
from src.parse_common import Candidates, finish, read_offer_sentence, segments, visible_text


def parse(pages: dict[str, str], op: dict, today: date) -> ParseResult:
    offer_url, lobby_url = op["pages"]["offer"], op["pages"].get("lobby")
    page = soup(pages["offer"])
    h1 = page.find("h1")
    headline = visible_text(h1) if h1 else ""
    offer_terms = block_segments(pages["offer"], "expandable-text")
    promo_terms = block_segments(pages["offer"], "terms")
    banner = _lobby_banner(pages["lobby"]) if "lobby" in pages else []

    c = Candidates()
    for s in [headline, *offer_terms]:
        read_offer_sentence(c, s, offer_url)
    for s in banner:
        read_offer_sentence(c, s, lobby_url)
    for s in promo_terms:
        # "Betting more than £15 per spin on slots while using bonus funds."
        if m := re.search(r"more than\s+(£[\d.,]+)\s+per spin", s, re.IGNORECASE):
            c.money("max_bet", m.group(1), offer_url, s, with_currency=False)

    offer = new_offer(op, today, _offer_id(offer_url), offer_url,
                      terms_text="\n\n".join([headline, " ".join(offer_terms), " ".join(promo_terms)]),
                      reward_type="free_spins")
    issues = finish(offer, c, PARSED_FIELDS)
    return ParseResult([offer], issues)


def _lobby_banner(html: str) -> list[str]:
    """Sentences of the smallest lobby element that holds the welcome-offer small print."""
    page = soup(html)
    hits = [el for el in page.find_all(string=re.compile(r"New GB customers only"))]
    if not hits:
        raise LayoutChanged("lobby: welcome offer banner not found")
    return segments(hits[0].parent)


def _offer_id(url: str) -> str:
    promo = parse_qs(urlsplit(url).query).get("promo", ["welcome"])[0]
    return f"unibet_uk:{promo}"
