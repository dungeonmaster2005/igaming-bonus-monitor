"""Unibet UK casino welcome offer.

Page layout (server-rendered, CSS class names are hashed, so only ids are used):
  <h1>                      headline ("Get 300 FREE SPINS when you play £30 on slots")
  #expandable-text          offer-specific "Full Terms and Conditions"
  #terms                    general Promotional Terms (max bet rules live here)
"""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from src.models import Offer
from src.parse_common import Ambiguous, Candidates, parse_days, parse_money, parse_wagering, sentences


class LayoutChanged(Exception):
    """The page no longer has the blocks this parser relies on."""


def _block(soup: BeautifulSoup, element_id: str) -> str:
    element = soup.find(id=element_id)
    if element is None:
        raise LayoutChanged(f"#{element_id} not found")
    return " ".join(element.get_text(" ").split())


def parse(pages: dict[str, str], op: dict, today: date) -> list[Offer]:
    soup = BeautifulSoup(pages["offer"], "html.parser")
    h1 = soup.find("h1")
    headline = " ".join(h1.get_text(" ").split()) if h1 else ""
    offer_terms = _block(soup, "expandable-text")
    promo_terms = _block(soup, "terms")

    c = Candidates()
    for s in sentences(offer_terms):
        _read_offer_sentence(c, s)
    for s in sentences(promo_terms):
        _read_promo_sentence(c, s)

    offer = Offer(
        date=today, operator_id=op["id"], operator=op["operator"], market=op["market"],
        license=op["license"], url=op["pages"]["offer"], offer_id=_offer_id(op),
        reward_type="free_spins",
        terms_text="\n\n".join(part for part in (headline, offer_terms, promo_terms) if part),
    )
    for name in ("min_deposit", "qualifying_amount", "free_spins", "spin_value", "currency",
                 "eligible_games", "wagering_multiplier", "wagering_applies_to", "max_bet",
                 "max_win", "claim_days", "use_days", "expiry_days", "promo_code"):
        value, evidence = c.resolve(name)
        setattr(offer, name, value)
        if evidence:
            offer.evidence[name] = evidence

    deposit, wager = offer.min_deposit, offer.qualifying_amount
    if deposit is not None and wager is not None:
        offer.qualifying_action = "deposit+wager"
    elif deposit is not None:
        offer.qualifying_action, offer.qualifying_amount = "deposit", deposit
    elif wager is not None:
        offer.qualifying_action = "wager"
    if offer.free_spins is not None and offer.spin_value is not None:
        offer.total_reward_value = offer.free_spins * offer.spin_value
    return [offer]


def _offer_id(op: dict) -> str:
    promo = parse_qs(urlsplit(op["pages"]["offer"]).query).get("promo", ["welcome"])[0]
    return f"{op['id']}:{promo}"


def _money(c: Candidates, name: str, text: str, sentence: str) -> None:
    try:
        money = parse_money(text)
    except Ambiguous as exc:
        c.ambiguous(name, str(exc), sentence)
        return
    if money:
        c.add(name, money.amount, sentence)
        c.add("currency", money.currency, sentence)


def _read_offer_sentence(c: Candidates, s: str) -> None:
    low = s.lower()
    if m := re.search(r"\bdeposit\s+(£[\d.,]+)\+?", s, re.IGNORECASE):
        _money(c, "min_deposit", m.group(1), s)
    if m := re.search(r"\b(?:stake|played?)\s+(£[\d.,]+)\s+on\s+slot", s, re.IGNORECASE):
        _money(c, "qualifying_amount", m.group(1), s)
    if m := re.search(r"\b(\d+)\s+free spins\b", s, re.IGNORECASE):
        c.add("free_spins", int(m.group(1)), s)
    if m := re.search(r"free spins\s+(?:of\s+)?(?:value|worth)\s+(?:of\s+)?(\S+)", s, re.IGNORECASE):
        _money(c, "spin_value", m.group(1), s)
    if m := re.search(r"(?i:free spins)(?: of value \S+)? (?:on|at) ([A-Z][\w' ]+?)(?:[,.]| will| when|$)", s):
        c.add("eligible_games", m.group(1).strip(), s)
    if (mult := parse_wagering(s)) is not None:
        c.add("wagering_multiplier", mult, s)
        # Only sentences that say WHAT is wagered set the base. In a free-spins offer
        # "bonus funds" are the spin winnings, so those sentences give the multiplier only.
        if "winnings" in low:
            c.add("wagering_applies_to", "winnings", s)
        elif "deposit" in low and "bonus" in low:
            c.add("wagering_applies_to", "deposit+bonus", s)
    days = parse_days(s)
    if days is not None:
        if "to claim" in low:
            c.add("claim_days", days, s)
        elif re.search(r"\bto use\b|\bexpire", low) and "spin" in low:
            c.add("use_days", days, s)
        elif "wagering requirement" in low and "within" in low:
            c.add("expiry_days", days, s)
    if m := re.search(r"\b(?:promo|bonus)?\s*code\s+([A-Z0-9]{3,})\b", s):
        c.add("promo_code", m.group(1), s)
    if re.search(r"max(?:imum)?\.? win|winnings? (?:are )?capped", low):
        if (money := _safe_money(s)) is not None:
            c.add("max_win", money, s)


def _read_promo_sentence(c: Candidates, s: str) -> None:
    # "Betting more than £15 per spin on slots while using bonus funds."
    if m := re.search(r"more than\s+(£[\d.,]+)\s+per spin", s, re.IGNORECASE):
        _money(c, "max_bet", m.group(1), s)


def _safe_money(s: str):
    try:
        money = parse_money(s)
    except Ambiguous:
        return None
    return money.amount if money else None
