"""UK operators whose offer is one free-spins reward described on 1–2 pages.

MrQ:     /promotions/first-deposit-spins ("What will I get?" … "General T&Cs") + home hero.
NetBet:  /welcome-bonus-100 (whole page body) + home promo banner.
Midnite: help-centre article (list items without full stops → block segmentation).
"""

from __future__ import annotations

import re
from datetime import date

from src.models import PARSED_FIELDS, ParseResult
from src.operators.base import LayoutChanged, between, new_offer, soup
from src.parse_common import Candidates, finish, read_offer_sentence, segments


def _run(op: dict, today: date, blocks: list[tuple[str, list[str]]], offer_id: str):
    c = Candidates()
    for url, items in blocks:
        for s in items:
            read_offer_sentence(c, s, url)
    offer = new_offer(op, today, offer_id, op["pages"]["offer"], reward_type="free_spins",
                      terms_text="\n\n".join(" ".join(items) for _, items in blocks))
    return c, offer


def parse_mrq(pages: dict[str, str], op: dict, today: date) -> ParseResult:
    offer_items = between(segments(soup(pages["offer"])), r"^What will I get\?", r"^General T&Cs")
    if not offer_items:
        raise LayoutChanged("mrq offer: 'What will I get?' block not found")
    home_items = between(segments(soup(pages["home"])), r"Free Spins", r"Full T&Cs apply", include_start=True)
    home_items = [s for s in home_items if "Free Spins" in s or "spin" in s.lower() or "winnings" in s.lower()]
    c, offer = _run(op, today, [(op["pages"]["offer"], offer_items), (op["pages"]["home"], home_items)],
                    "mrq:first-deposit-spins")
    return ParseResult([offer], finish(offer, c, PARSED_FIELDS))


def parse_netbet(pages: dict[str, str], op: dict, today: date) -> ParseResult:
    offer_items = between(segments(soup(pages["offer"])), r"Bet £\d+ & Get \d+ Free Spins", r"^Activate Quest")
    if not offer_items:
        raise LayoutChanged("netbet offer: headline not found")
    home_items = between(segments(soup(pages["home"])), r"New customers only", r"T&Cs apply", include_start=True)
    home_items = [s for s in home_items if not re.search(r"NETBET MAX|Level-ups", s, re.IGNORECASE)]
    c, offer = _run(op, today, [(op["pages"]["offer"], offer_items), (op["pages"]["home"], home_items)],
                    "netbet_uk:welcome-bonus-100")
    return ParseResult([offer], finish(offer, c, PARSED_FIELDS))


def parse_midnite(pages: dict[str, str], op: dict, today: date) -> ParseResult:
    page = soup(pages["offer"])
    article = page.find("article") or page.find("main")
    if article is None:
        raise LayoutChanged("midnite: article not found")
    items = between(segments(article), r"Who.s it for\?", r"^Related Articles")
    url = op["pages"]["offer"]
    c, offer = _run(op, today, [(url, items)], "midnite:uk-casino-welcome-offer")
    for s in items:
        # The list item "Expires 7 days after crediting" sits under "What do I get? 100 Free Spins".
        if m := re.search(r"^Expires (\d+) days after crediting", s):
            c.add("use_days", int(m.group(1)), url, s)
    return ParseResult([offer], finish(offer, c, PARSED_FIELDS))
