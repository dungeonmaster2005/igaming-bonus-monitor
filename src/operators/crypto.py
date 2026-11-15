"""Crypto casinos (Curaçao CGA licence matched to the register).

A welcome package spans several deposits; each deposit step is one row and the steps share
an offer_id (offer_label "Deposit N of M"). Amounts are kept in BTC as the operators state
them; package-wide rules (wagering, max bet, expiry) come from the bonus terms.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from src.models import PARSED_FIELDS, Issue, Offer, ParseResult, Quote
from src.operators.base import LayoutChanged, between, new_offer, soup
from src.parse_common import Candidates, finish, parse_crypto_amount, parse_wagering, segments

ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "1st": 1, "2nd": 2, "3rd": 3, "4th": 4}


def _crypto(c: Candidates, name: str, text: str, url: str, s: str, currency: bool = True) -> None:
    money = parse_crypto_amount(text)
    if money:
        c.add(name, money.amount, url, s)
        if currency:
            c.add("currency", money.currency, url, s)


def _rows(op, today, offer_id, url, steps: dict[int, Candidates], shared: Candidates, terms_text: str,
          reward_type="bonus_cash") -> tuple[list[Offer], list[Issue]]:
    offers, issues = [], []
    total = len(steps)
    for n in sorted(steps):
        c = steps[n]
        for name, found in shared.values.items():  # package-wide rules apply to every step
            for value, quote in found:
                c.values.setdefault(name, []).append((value, quote))
        for name, found in shared.unclear.items():
            c.unclear.setdefault(name, []).extend(found)
        c.notes.update({k: v for k, v in shared.notes.items() if k not in c.notes})
        offer = new_offer(op, today, offer_id, url, reward_type=reward_type,
                          offer_label=f"Deposit {n} of {total}", terms_text=terms_text)
        issues += finish(offer, c, PARSED_FIELDS)
        offer.qualifying_action = "deposit"
        if offer.bonus_percent and offer.max_bonus is not None:
            needed = offer.max_bonus * 100 / offer.bonus_percent
            offer.qualifying_amount = needed.quantize(Decimal("0.00000001")).normalize()
            offer.evidence["qualifying_amount"] = offer.evidence.get("max_bonus")
        offers.append(offer)
    return offers, issues


def _all(html: str) -> list[str]:
    return segments(soup(html))


# -- BitStarz -------------------------------------------------------------------

def parse_bitstarz(pages: dict[str, str], op: dict, today: date) -> ParseResult:
    u = op["pages"]
    steps: dict[int, Candidates] = {n: Candidates() for n in (1, 2, 3, 4)}
    shared = Candidates()
    promo = _all(pages["promotions"])
    home = _all(pages["offer"])
    terms = _all(pages["terms"])
    step_patterns = [
        (1, re.compile(r"first deposit we.ll give you a (\d+)% up to ([\d.]+ BTC) \+ (\d+) Free Spins", re.I)),
        (2, re.compile(r"Second Deposit, we.ll give you (\d+)% up to ([\d.]+ BTC)", re.I)),
        (3, re.compile(r"Third Deposit, you.ll get another (\d+)% up to ([\d.]+ BTC)", re.I)),
        (4, re.compile(r"4th Welcome Deposit offer, .*?(\d+)% up to ([\d.]+ BTC)", re.I)),
    ]
    for s in promo:
        for n, pattern in step_patterns:
            if m := pattern.search(s):
                steps[n].add("bonus_percent", Decimal(m.group(1)), u["promotions"], s)
                _crypto(steps[n], "max_bonus", m.group(2), u["promotions"], s)
                if m.lastindex >= 3:
                    steps[n].add("free_spins", int(m.group(3)), u["promotions"], s)
        if m := re.search(r"all you gotta do is put in ([\d.]+ BTC)", s):
            _crypto(steps[1], "min_deposit", m.group(1), u["promotions"], s, currency=False)
    for s in home:
        if m := re.search(r"\b(First|Second|Third|Fourth) deposit: (\d+)% up to (?:EUR [\d,]+)?(?: or ([\d.]+ BTC))?"
                          r"(?:, plus (\d+) free spins)?", s):
            n = ORDINALS[m.group(1).lower()]
            steps[n].add("bonus_percent", Decimal(m.group(2)), u["offer"], s)
            if m.group(3):
                _crypto(steps[n], "max_bonus", m.group(3), u["offer"], s)
            if m.group(4):
                steps[n].add("free_spins", int(m.group(4)), u["offer"], s)
        if m := re.search(r"Wagering is (\d+)x", s):
            shared.add("wagering_multiplier", Decimal(m.group(1)), u["offer"], s)
    for s in terms:
        if m := re.search(r"All bonuses \(unless otherwise specified\) need to be wagered (\d+) \(\w+\) times", s):
            shared.add("wagering_multiplier", Decimal(m.group(1)), u["terms"], s)
            shared.add("wagering_applies_to", "bonus", u["terms"], s)
        if "maximum allowable bet with a bonus active" in s and (m := re.search(r"or ([\d.]+ BTC)", s)):
            _crypto(shared, "max_bet", m.group(1), u["terms"], s, currency=False)
        if m := re.search(r"All bonuses that are a part of the Welcome Package are valid for (\d+) days", s):
            shared.add("expiry_days", Decimal(m.group(1)), u["terms"], s)
        if re.search(r"activate your free spins you must access and open the game within 1 day", s):
            steps[1].note("use_days", "two windows: open the game within 1 day of crediting, then finish the "
                                      "rounds within 1 day of activation")
    # terms text: the welcome-package blocks and the bonus terms only — the rest of these pages is a
    # live lobby (24-hour wins ticker, rotating game carousels) that changes every hour
    promo_block = between(promo, r"^Welcome Package$", r"^Level Up Adventure$")
    home_block = between(home, r"^Four deposits, four bonuses", r"welcome package is just the opening act")
    if not promo_block or not home_block:
        raise LayoutChanged("bitstarz: welcome package block not found")
    offers, issues = _rows(op, today, "bitstarz:welcome-package", u["promotions"], steps, shared,
                           "\n\n".join([" ".join(promo_block), " ".join(home_block), " ".join(terms)]))
    return ParseResult(offers, issues)


# -- 7Bit and KatsuBet (Kika Technology B.V.) -----------------------------------

def parse_sevenbit(pages: dict[str, str], op: dict, today: date) -> ParseResult:
    u = op["pages"]
    terms = _all(pages["terms"])
    welcome = between(terms, r"^Welcome Package:", r"Match bonuses are applied")
    if not welcome:
        raise LayoutChanged("7bit terms: 'Welcome Package:' block not found")
    steps: dict[int, Candidates] = {}
    shared = Candidates()
    current = None
    for s in welcome:
        if m := re.search(r"\b(First|Second|Third|Fourth) deposit bonus: (\d+)% up to ([\d.]+ BTC)"
                          r"(?: \+ (\d+) free spins)?", s):
            current = ORDINALS[m.group(1).lower()]
            c = steps.setdefault(current, Candidates())
            c.add("bonus_percent", Decimal(m.group(2)), u["terms"], s)
            _crypto(c, "max_bonus", m.group(3), u["terms"], s)
            if m.group(4):
                c.add("free_spins", int(m.group(4)), u["terms"], s)
            continue
        if m := re.search(r"minimum deposit requirement to qualify for any part of welcome bonus is ([\d.]+ mBTC)", s):
            _crypto(shared, "min_deposit", m.group(1), u["terms"], s, currency=False)
        elif m := re.search(r"maximum bet per spin while wagering any bonus from Welcome Package is (\d+ μBTC)", s):
            _crypto(shared, "max_bet", m.group(1), u["terms"], s, currency=False)
        elif re.search(r"^All bonuses \(unless otherwise specified\) need to be wagered", s):
            shared.add("wagering_multiplier", parse_wagering(s), u["terms"], s)
            shared.add("wagering_applies_to", "bonus", u["terms"], s)
        elif m := re.search(r"Bonuses expire after (\d+) days if the wager is not covered", s):
            shared.add("expiry_days", Decimal(m.group(1)), u["terms"], s)
        elif current is not None:
            if m := re.search(r"enter the bonus code (\w+)", s):
                steps[current].add("promo_code", m.group(1), u["terms"], s)
            if re.search(r"granted automatically|comes without any coupon code", s):
                steps[current].none("promo_code", u["terms"], s)
            if m := re.search(r"played in the (.+?) game by", s):
                steps[current].add("eligible_games", m.group(1), u["terms"], s)
    offers, issues = _rows(op, today, "sevenbit:welcome-package", u["terms"], steps, shared, " ".join(welcome))
    issues += _package_total(op, today, "sevenbit:welcome-package", offers, _all(pages["offer"]), u["offer"])
    return ParseResult(offers, issues)


def _package_total(op, today, offer_id, offers, page, url) -> list[Issue]:
    """Headline package maximum vs the sum of the per-deposit maximums."""
    for s in page:
        if m := re.search(r"(\d+)% match up to ([\d.]+) BTC and (\d+) Free Spins", s):
            stated = Decimal(m.group(2))
            summed = sum((o.max_bonus or Decimal(0)) for o in offers)
            if all(o.max_bonus is not None for o in offers) and summed != stated:
                return [Issue(date=today, operator_id=op["id"], operator=op["operator"], offer_id=offer_id,
                              field="max_bonus (package total)", type="conflict",
                              quotes=[Quote(url, s)] + [o.evidence["max_bonus"] for o in offers],
                              detail=f"headline total {stated} BTC; sum of the four deposit steps {summed} BTC")]
    return []


def parse_katsubet(pages: dict[str, str], op: dict, today: date) -> ParseResult:
    u = op["pages"]
    raw = _all(pages["terms"])
    # Cards put the value and its label in separate elements: "50" + "Wager for FS".
    joined, i = [], 0
    while i < len(raw):
        if re.fullmatch(r"\$?\d+(?:\.\d+)?", raw[i]) and i + 1 < len(raw):
            joined.append(f"{raw[i]} {raw[i + 1]}")
            i += 2
        else:
            joined.append(raw[i])
            i += 1
    items = list(dict.fromkeys(joined))  # the landing block is repeated for mobile
    c = Candidates()
    from src.parse_common import read_offer_sentence
    for s in items:
        if not re.search(r"\$\d|free spins|wager|code", s, re.I):
            continue
        read_offer_sentence(c, s, u["terms"])
        if m := re.search(r"Make a \$(\d+(?:\.\d+)?) deposit", s):
            c.add("min_deposit", Decimal(m.group(1)), u["terms"], s)
            c.add("currency", "USD", u["terms"], s)
        if re.search(r"\d+ Wager for FS", s):
            c.add("wagering_applies_to", "winnings", u["terms"], s)
    offer = new_offer(op, today, "katsubet:1bet", u["terms"], reward_type="free_spins",
                      offer_label="First deposit", terms_text=" ".join(items))
    return ParseResult([offer], finish(offer, c, PARSED_FIELDS))


# -- mBit -----------------------------------------------------------------------

def parse_mbit(pages: dict[str, str], op: dict, today: date) -> ParseResult:
    u = op["pages"]
    page = _all(pages["offer"])
    terms = _all(pages["terms"])
    steps: dict[int, Candidates] = {n: Candidates() for n in (1, 2, 3)}
    shared = Candidates()
    heading = None  # "Nth Deposit Bonus" heading; the "Claim a …" line under it belongs to that step
    for s in page:
        if m := re.fullmatch(r"(1st|2nd|3rd) Deposit Bonus", s):
            heading = ORDINALS[m.group(1)]
            continue
        if heading and (m := re.search(r"Claim a (\d+)% deposit match up to ([\d.]+ BTC) \+ (\d+) Free Spins", s)):
            steps[heading].add("bonus_percent", Decimal(m.group(1)), u["offer"], s)
            _crypto(steps[heading], "max_bonus", m.group(2), u["offer"], s)
            steps[heading].add("free_spins", int(m.group(3)), u["offer"], s)
        heading = None
    # The bonus table is built from <div> cells; its "Min Deposit" column holds one cell per step.
    min_cells = [s for s in page if re.fullmatch(r"\d+ EUR(?: \(or crypto equivalent\))?", s)]
    if len(min_cells) == len(steps):
        for n, cell in zip(sorted(steps), min_cells):
            steps[n].note("min_deposit", f"“{cell}” — not in the bonus currency (BTC)")
    for s in terms:
        if m := re.search(r"First Deposit Bonus and Second Deposit Bonus each have a wager requirement of (\d+)x", s):
            for n in (1, 2):
                steps[n].add("wagering_multiplier", Decimal(m.group(1)), u["terms"], s)
                steps[n].add("wagering_applies_to", "bonus", u["terms"], s)
        if m := re.search(r"Third Deposit Bonus has a wager requirement of (\d+)x", s):
            steps[3].add("wagering_multiplier", Decimal(m.group(1)), u["terms"], s)
            steps[3].add("wagering_applies_to", "bonus", u["terms"], s)
        if m := re.search(r"deposit bonuses are valid for (\d+) day", s):
            shared.add("expiry_days", Decimal(m.group(1)), u["terms"], s)
        if m := re.search(r"free spins bonuses are available for (\d+) day", s):
            shared.add("use_days", Decimal(m.group(1)), u["terms"], s)
        if "maximum bet that can be placed is" in s and (m := re.search(r"is ([\d.]+ BTC)", s)):
            _crypto(shared, "max_bet", m.group(1), u["terms"], s, currency=False)
        if m := re.search(r"maximum win of Free Spins included in any welcome package is limited to .*?([\d.]+ BTC)", s):
            _crypto(shared, "max_win", m.group(1), u["terms"], s, currency=False)
    offers, issues = _rows(op, today, "mbit:welcome-package", u["offer"], steps, shared,
                           "\n\n".join([" ".join(page), " ".join(terms)]))
    return ParseResult(offers, issues)
