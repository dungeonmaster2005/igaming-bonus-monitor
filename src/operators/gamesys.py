"""Gamesys brands (Virgin Games, Jackpotjoy, Monopoly Casino, Rainbow Riches Casino).

All four share one site template and one rules page (/uk/terms or /terms):
  "CASINO WELCOME OFFER RULES <N> Free Spins ... 1. ... 11."  (then BINGO / SPORTS sections)
Offer page: either a dedicated welcome page (Virgin, Monopoly) or the home page, whose
promo banners carry the small print "New members only ... 18+".
Monopoly lets the player choose free spins OR free bets: two rows, one offer_id.
"""

from __future__ import annotations

import re
from datetime import date

from src.models import PARSED_FIELDS, Issue, ParseResult
from src.operators.base import LayoutChanged, between, new_offer, soup
from src.parse_common import Candidates, finish, read_offer_sentence, segments

_RULES_STOP = r"(?:BINGO|SPORTS|SLOTS|LIVE)\b.*OFFER RULES|is licensed and regulated|Contact Us"
_SPORTS = re.compile(r"free bet|sportsbook|odds|sports", re.IGNORECASE)


def parse(pages: dict[str, str], op: dict, today: date) -> ParseResult:
    offer_url, terms_url = op["pages"]["offer"], op["pages"]["terms"]
    rules_all = segments(soup(pages["terms"]))
    casino_rules = between(rules_all, r"^CASINO WELCOME OFFER RULES|^WELCOME OFFER RULES", _RULES_STOP)
    if not casino_rules:
        raise LayoutChanged("terms: CASINO WELCOME OFFER RULES not found")
    sports_rules = between(rules_all, r"^SPORTS WELCOME OFFER RULES", r"(?:BINGO|CASINO)\b.*OFFER RULES"
                           r"|is licensed and regulated|Contact Us")
    offer_text = _offer_sentences(pages["offer"], dedicated="welcome" in offer_url)
    casino_text = [s for s in offer_text if not _SPORTS.search(s)]
    sports_text = [s for s in offer_text if _SPORTS.search(s)]

    offer_id = f"{op['id']}:welcome"
    c = Candidates()
    for s in casino_rules:
        read_offer_sentence(c, s, terms_url)
    for s in casino_text:
        read_offer_sentence(c, s, offer_url)
    casino = new_offer(op, today, offer_id, offer_url, reward_type="free_spins",
                       terms_text="\n\n".join([" ".join(casino_rules), " ".join(casino_text)]))
    issues = finish(casino, c, PARSED_FIELDS)
    issues += _period_start_differs(casino, casino_rules, casino_text, terms_url, offer_url)
    offers = [casino]

    if sports_rules:  # Monopoly: the alternative reward
        casino.offer_label = "Choice A: free spins"
        fb = Candidates()
        for s in sports_rules + sports_text:
            url = terms_url if s in sports_rules else offer_url
            _read_free_bet_sentence(fb, s, url)
        bets = new_offer(op, today, offer_id, offer_url, reward_type="free_bets", offer_label="Choice B: free bets",
                         product="sports",
                         eligible_games=None,
                         terms_text="\n\n".join([" ".join(sports_rules), " ".join(sports_text)]))
        issues += finish(bets, fb, PARSED_FIELDS)
        offers.append(bets)
    return ParseResult(offers, issues)


def _offer_sentences(html: str, dedicated: bool) -> list[str]:
    """Dedicated welcome page: its main content. Home page: only the welcome-offer banners."""
    page = soup(html)
    main = page.find("main") or page.body or page
    items = segments(main)
    if dedicated:
        return items
    out, block = [], []
    for s in items:
        if re.search(r"^(?:\*+)?New (?:UK )?members only", s) or block:
            block.append(s)
            if "18+" in s:
                if any("Free Spins" in b for b in block) and not any("bingo" in b.lower() for b in block):
                    out.extend(block)
                block = []
        elif re.search(r"\b(?:Play|Bet) £\d+, Get \d+ Free Spins", s):
            out.append(s)
    return out


def _read_free_bet_sentence(c: Candidates, s: str, url: str) -> None:
    if m := re.search(r"(£\d+) in free bets", s, re.IGNORECASE):
        c.money("max_bonus", m.group(1), url, s)
    if m := re.search(r"deposit\s*&\s*bet\s*min\.?\s*(£\d+)|first deposit of at least (£\d+)", s, re.IGNORECASE):
        c.money("min_deposit", m.group(1) or m.group(2), url, s, with_currency=False)
    if m := re.search(r"deposit\s*&\s*bet\s*min\.?\s*(£\d+)|total value of at least (£\d+)", s, re.IGNORECASE):
        c.money("qualifying_amount", m.group(1) or m.group(2), url, s, with_currency=False)
    if m := re.search(r"odds of (\d+/\d+(?: \([\d.]+\))?) or greater|min\.? (\d+/\d+) odds", s, re.IGNORECASE):
        c.add("eligible_games", f"sportsbook, odds {m.group(1) or m.group(2)} or greater", url, s)
    if m := re.search(r"(\d+) days from when you make your first deposit to place the Qualifying Bet", s):
        c.add("claim_days", int(m.group(1)), url, s)
    if m := re.search(r"valid (\d+) days", s):
        c.add("use_days", int(m.group(1)), url, s)


def _period_start_differs(offer, rules: list[str], page: list[str], rules_url: str, page_url: str) -> list[Issue]:
    """Virgin: rules count the qualifying period from the first deposit, the offer page from registration."""
    from_deposit = [s for s in rules if re.search(r"days from when you make your first deposit to complete", s)]
    from_reg = [s for s in page if re.search(r"days from the date of registration to complete", s)]
    if not (from_deposit and from_reg):
        return []
    from src.models import Quote
    return [Issue(date=offer.date, operator_id=offer.operator_id, operator=offer.operator, offer_id=offer.offer_id,
                  field="claim_days", type="conflict",
                  quotes=[Quote(rules_url, from_deposit[0]), Quote(page_url, from_reg[0])],
                  detail="same number of days, different start: first deposit (rules) vs registration (offer page)")]
