"""Spin Genie: the player picks ONE of three welcome offers → rows with a shared offer_id.

A  120 free spins on Book of Dead (default; /promotions/free-spins/ + the home/bonus-policy banner)
B  100% match up to £25 + 50 free spins, code SNG50 (/promotions/slots-welcome-bonus/)
C  "Free Spins for a Year" (/promotions/free-spins-for-a-year/), when that page was fetched
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from src.models import PARSED_FIELDS, ParseResult
from src.operators.base import between, new_offer, soup
from src.parse_common import Candidates, finish, read_offer_sentence, segments

OFFER_ID = "spin_genie:welcome-choice"
_BANNER = r"Automatically credited on 1st Deposit"
_MAX_BET_RULE = re.compile(r"Max bet:\s*\d+%.*?or £\d+ \(lowest applies\)")


def _main(html: str) -> list[str]:
    page = soup(html)
    return between(segments(page.find("main") or page), r".", r"^Trusted payment providers|^Follow us on")


def parse(pages: dict[str, str], op: dict, today: date) -> ParseResult:
    urls = op["pages"]
    offers, issues = [], []

    # A — default 120 free spins
    c = Candidates()
    # Each offer page also advertises the other two; those sentences belong to the other rows.
    fs_items = [s for s in _main(pages["free_spins"])
                if not re.search(r"other two welcome offers|Other Casino Welcome|Match Up to|SNG50|for a Year|\| Spin Genie", s)]
    banner = [(urls[k], s) for k in ("home", "terms") for s in between(segments(soup(pages[k])), _BANNER, r"Bonus Policy applies")]
    for s in fs_items:
        read_offer_sentence(c, s, urls["free_spins"])
    for url, s in banner:
        read_offer_sentence(c, s, url)
        if _MAX_BET_RULE.search(s):
            c.note("max_bet", "conditional limit: “" + _MAX_BET_RULE.search(s).group(0) + "”")
    a = new_offer(op, today, OFFER_ID, urls["free_spins"], reward_type="free_spins",
                  offer_label="Choice A: 120 free spins (default)",
                  terms_text="\n\n".join([" ".join(fs_items), " ".join(s for _, s in banner)]))
    issues += finish(a, c, PARSED_FIELDS)
    offers.append(a)

    # B — 100% match + 50 free spins, code SNG50
    c = Candidates()
    items = [s for s in _main(pages["offer"])
             if not re.search(r"Even More UK Casino|check out our default|^120 Free Spins|for a Year|Book of Dead|\| Spin Genie", s)]
    for s in items:
        read_offer_sentence(c, s, urls["offer"])
        if m := re.search(r"(\d+)% Match Up to (£\d+)", s, re.IGNORECASE):
            c.add("bonus_percent", Decimal(m.group(1)), urls["offer"], s)
            c.money("max_bonus", m.group(2), urls["offer"], s)
    c.note("wagering_multiplier", "not stated on the offer page; the general Bonus Policy (47-MGA-N) only gives "
                                  "an example: “wager 30 times the aggregate amount … (deposit + bonus)” in €")
    b = new_offer(op, today, OFFER_ID, urls["offer"], reward_type="bonus_cash",
                  offer_label="Choice B: 100% match up to £25 + 50 free spins", terms_text=" ".join(items))
    issues += finish(b, c, PARSED_FIELDS)
    _deposit_for_full_bonus(b)
    offers.append(b)

    # C — Free Spins for a Year (only if the page is configured and was fetched)
    if "year" in pages:
        c = Candidates()
        items = [s for s in _main(pages["year"])
                 if not re.search(r"(?i)other (?:two )?welcome offers|Other Casino Welcome|Match Up to|SNG50"
                                  r"|120 Free Spins|\| Spin Genie|— Spin Genie", s)]
        for s in items:
            read_offer_sentence(c, s, urls["year"])
            if m := re.search(r"one of our slot games — ([A-Z][\w' ]+)!", s):
                c.add("eligible_games", m.group(1), urls["year"], s)
        # "10 spins awarded each week … a total of 520 free spins": the row holds the total.
        weekly = [(v, q) for v, q in c.values.get("free_spins", []) if "total of" not in q.text]
        c.values["free_spins"] = [(v, q) for v, q in c.values.get("free_spins", []) if "total of" in q.text]
        if weekly:
            c.note("spin_value", "not stated; spins are awarded 10 per week for 52 weeks")
        y = new_offer(op, today, OFFER_ID, urls["year"], reward_type="free_spins",
                      offer_label="Choice C: free spins for a year", terms_text=" ".join(items))
        issues += finish(y, c, PARSED_FIELDS)
        offers.append(y)
    return ParseResult(offers, issues)


def _deposit_for_full_bonus(offer) -> None:
    """For a match bonus the qualifying amount is the deposit that unlocks the maximum bonus."""
    if offer.bonus_percent and offer.max_bonus is not None:
        needed = offer.max_bonus * 100 / offer.bonus_percent
        wager = offer.qualifying_amount or Decimal(0)
        offer.qualifying_amount = max(needed, wager)
        offer.qualifying_action = "deposit+wager" if offer.min_deposit or wager else "deposit"
        offer.evidence["qualifying_amount"] = offer.evidence.get("max_bonus")
