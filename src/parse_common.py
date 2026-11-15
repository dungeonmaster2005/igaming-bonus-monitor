"""Shared parsing of amounts, wagering, limits and durations in bonus terms.

Parsers collect every candidate value for a field together with the official page and the
verbatim sentence it came from (`Candidates`). Resolution rule (user decision, phase 2):
- unambiguous candidates that agree give the value;
- ambiguous phrasings or typos never give a value, they are reported as issues;
- if the unambiguous candidates disagree, the field stays empty and a conflict is reported.
A phrase that explicitly says a limit does not exist ("no cap on winnings", "no code
needed") is recorded as `stated_none`, which is different from "not mentioned".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from bs4 import BeautifulSoup, NavigableString

from src.models import Issue, Offer, Quote

CURRENCY_SYMBOLS = {"£": "GBP", "€": "EUR", "$": "USD"}
STATED_NONE = "<stated: none>"

_MONEY = re.compile(
    r"(?P<sym>[£€$])\s?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"|(?<![\w.£€$])(?P<pence>\d+(?:\.\d+)?)\s?p\b"
)
_CRYPTO = re.compile(
    r"(?P<num>\d+(?:[.,]\d+)?)\s?(?P<unit>μBTC|µBTC|uBTC|mBTC|BTC|EUR|USD|USDT)\b"
)
_DURATION = re.compile(r"(?P<n>\d+)\s*(?P<unit>hours?|hrs?|days?)\b", re.IGNORECASE)
# Not after a question whose answer follows ("Promo Code? No.").
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9•(*])(?!(?:No|Yes)\b)")
# Table cells are NOT boundaries: a table row reads as one phrase ("1st Deposit 100% … Up to 1 BTC").
_BLOCK_TAGS = ["p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "dt", "dd", "br",
               "div", "section", "article", "header", "footer", "ul", "ol", "table"]


class Ambiguous(ValueError):
    """A phrase that has more than one reasonable reading (e.g. "0.10p")."""


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str


def parse_money(text: str) -> Money | None:
    """First amount in `text`: "£30", "£1,000", "€0.20", "10p" (= £0.10).

    "0.10p" is ambiguous — a tenth of a penny or (sloppily) 10p — and raises Ambiguous.
    """
    match = _MONEY.search(text)
    if not match:
        return None
    if match.group("pence"):
        pence = match.group("pence")
        if "." in pence:
            raise Ambiguous(f"{match.group(0)!r}: decimal amount with a pence suffix")
        return Money(Decimal(pence) / 100, "GBP")
    return Money(Decimal(match.group("num").replace(",", "")), CURRENCY_SYMBOLS[match.group("sym")])


def parse_crypto_amount(text: str) -> Money | None:
    """First "<number> <unit>" amount: "1.5 BTC", "13 μBTC" (→ 0.000013 BTC), "30 EUR"."""
    match = _CRYPTO.search(text)
    if not match:
        return None
    amount = Decimal(match.group("num").replace(",", "."))
    unit = match.group("unit")
    if unit == "mBTC":
        return Money(amount / 1000, "BTC")
    if unit in ("μBTC", "µBTC", "uBTC"):
        return Money(amount / 1_000_000, "BTC")
    return Money(amount, unit)


def parse_days(text: str) -> Decimal | None:
    """First duration in `text` as days: "within 7 days" → 7, "48 hours" → 2."""
    match = _DURATION.search(text)
    if not match:
        return None
    n = Decimal(match.group("n"))
    return n if match.group("unit").lower().startswith("d") else n / 24


_WAGER = re.compile(
    r"wager(?:ed|ing)?\s+(?:requirement\s+of\s+)?[x×]\s?(?P<a>\d{1,3})\b"
    r"|wager(?:ed|ing)?\s+(?:requirement\s+of\s+)?(?P<b>\d{1,3})\s?(?:x|×|times)(?!\w)"
    r"|(?P<c>\d{1,3})\s?[x×]\s+(?:wagering|omsättningskrav)"
    r"|\bWR\s+(?P<d>\d{1,3})\s?x"
    r"|wager(?:ed)?\s+(?:the\s+)?(?:bonus|winnings|deposit)(?:\s*\+\s*bonus)?\s+[x×]\s?(?P<e>\d{1,3})\b"
    r"|wagering requirement of (?P<f>\d{1,3})\b"
    r"|\b(?P<g>\d{1,3})\s+wager for",
    re.IGNORECASE,
)
_NO_WAGER = re.compile(
    r"no wagering|wager[- ]free|without wagering requirements?|have no wagering requirements?"
    r"|no wagering requirements?", re.IGNORECASE)


def parse_wagering(text: str) -> Decimal | None:
    """Wagering multiplier: "wagered x10", "wagered 10x", "10x wagering", "WR 10x", "no wagering" → 0."""
    match = _WAGER.search(text)
    if match:
        return Decimal(next(g for g in match.groups() if g))
    if _NO_WAGER.search(text):
        return Decimal(0)
    return None


def sentences(text: str) -> list[str]:
    """Split terms into sentences (keeps "£0.10" and "x10." intact)."""
    return [s.strip() for s in _SENTENCE_END.split(" ".join(text.split())) if s.strip()]


def segments(element) -> list[str]:
    """Sentences of an HTML element, split at block boundaries first.

    Pages that list terms without full stops (headings, list items) would otherwise merge
    unrelated phrases into one "sentence".
    """
    soup = BeautifulSoup(str(element), "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_after(NavigableString("\n"))
        tag.insert_before(NavigableString("\n"))
    out: list[str] = []
    for line in soup.get_text(" ").split("\n"):
        for s in sentences(line):
            # A table/FAQ answer in its own cell belongs to the question: "Promo Code?" + "No."
            if out and out[-1].endswith("?") and re.fullmatch(r"(?:No|Yes)\b.{0,40}", s):
                out[-1] = f"{out[-1]} {s}"
            else:
                out.append(s)
    return out


def visible_text(element) -> str:
    """Text of an element with scripts/styles removed and whitespace collapsed."""
    soup = BeautifulSoup(str(element), "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    return " ".join(soup.get_text(" ").split())


# -- candidates and resolution --------------------------------------------------


@dataclass
class Resolution:
    value: object | None
    evidence: Quote | None
    issue_type: str | None = None       # conflict / ambiguous / typo
    issue_quotes: list[Quote] = field(default_factory=list)
    issue_detail: str = ""


@dataclass
class Candidates:
    """Every value seen for each field, with the official page and verbatim sentence."""

    values: dict[str, list[tuple[object, Quote]]] = field(default_factory=dict)
    unclear: dict[str, list[tuple[str, str, Quote]]] = field(default_factory=dict)  # (kind, reason, quote)
    notes: dict[str, str] = field(default_factory=dict)  # why a field is empty, when not an issue

    def add(self, name: str, value: object, url: str, sentence: str) -> None:
        self.values.setdefault(name, []).append((value, Quote(url, sentence)))

    def none(self, name: str, url: str, sentence: str) -> None:
        """The terms explicitly say this limit/code does not exist."""
        self.add(name, STATED_NONE, url, sentence)

    def ambiguous(self, name: str, reason: str, url: str, sentence: str) -> None:
        self.unclear.setdefault(name, []).append(("ambiguous", reason, Quote(url, sentence)))

    def typo(self, name: str, reason: str, url: str, sentence: str) -> None:
        self.unclear.setdefault(name, []).append(("typo", reason, Quote(url, sentence)))

    def note(self, name: str, reason: str) -> None:
        self.notes[name] = reason

    def money(self, name: str, text: str, url: str, sentence: str, with_currency: bool = True) -> None:
        """Add the first amount in `text` (and its currency), or record why it is ambiguous."""
        try:
            money = parse_money(text)
        except Ambiguous as exc:
            self.ambiguous(name, str(exc), url, sentence)
            return
        if money:
            self.add(name, money.amount, url, sentence)
            if with_currency:
                self.add("currency", money.currency, url, sentence)

    def resolve(self, name: str) -> Resolution:
        found = self.values.get(name, [])
        unclear = self.unclear.get(name, [])
        distinct = list(dict.fromkeys(value for value, _ in found))
        quotes = list(dict.fromkeys(q for _, q in found))  # same sentence may repeat
        unclear_quotes = list(dict.fromkeys(q for _, _, q in unclear))
        if len(distinct) > 1:
            listing = ", ".join("none" if v == STATED_NONE else str(v) for v in distinct)
            return Resolution(None, None, "conflict", quotes + unclear_quotes, f"values found: {listing}")
        value = distinct[0] if distinct else None
        evidence = quotes[0] if quotes else None
        if unclear:
            kind = "typo" if all(k == "typo" for k, _, _ in unclear) else "ambiguous"
            reasons = "; ".join(dict.fromkeys(reason for _, reason, _ in unclear))
            shown = "none" if value == STATED_NONE else value
            detail = reasons + (f"; unambiguous value used: {shown}" if value is not None else "")
            return Resolution(value, evidence, kind, unclear_quotes + quotes, detail)
        return Resolution(value, evidence)


def apply(c: Candidates, offer: Offer, names: tuple[str, ...] | list[str]) -> list[Issue]:
    """Copy resolved fields into `offer` (with evidence) and return the issues found."""
    issues = []
    for name in names:
        res = c.resolve(name)
        if res.value == STATED_NONE:
            offer.stated_none[name] = res.evidence
        elif res.value is not None:
            setattr(offer, name, res.value)
            if res.evidence:
                offer.evidence[name] = res.evidence
        if res.issue_type:
            issues.append(Issue(date=offer.date, operator_id=offer.operator_id, operator=offer.operator,
                                offer_id=offer.offer_id, field=name, type=res.issue_type,
                                quotes=res.issue_quotes, detail=res.issue_detail))
        if name in c.notes and getattr(offer, name) is None and name not in offer.stated_none:
            offer.field_notes[name] = c.notes[name]
    return issues


def finish(offer: Offer, c: Candidates, names: tuple[str, ...] | list[str]) -> list[Issue]:
    """apply() + qualifying_action from the amounts found + derived totals."""
    issues = apply(c, offer, names)
    deposit, wager = offer.min_deposit, offer.qualifying_amount
    if offer.qualifying_action is None:
        if deposit is not None and wager is not None:
            offer.qualifying_action = "deposit+wager"
        elif deposit is not None:
            offer.qualifying_action, offer.qualifying_amount = "deposit", deposit
            offer.evidence.setdefault("qualifying_amount", offer.evidence.get("min_deposit"))
        elif wager is not None:
            offer.qualifying_action = "wager"
    offer.derive_totals()
    return issues


# -- shared wording -------------------------------------------------------------

_SPIN_VALUE = [
    re.compile(r"(?:valued at|value of|of value|worth|value\s*:|spin value\s*:|has a value of)\s*"
               r"(?P<v>£\s?\d+(?:\.\d+)?|\d+(?:\.\d+)?\s?p)(?!\w)", re.IGNORECASE),
    re.compile(r"(?P<v>£\s?\d+(?:\.\d+)?|(?<![\w.])\d+(?:\.\d+)?\s?p)\s*(?:per (?:free )?spin|each)\b", re.IGNORECASE),
    re.compile(r"(?P<v>£\s?\d+(?:\.\d+)?)-value free spins", re.IGNORECASE),
    re.compile(r"\d+\s*x\s*(?P<v>£\s?\d+(?:\.\d+)?)\s+free spins", re.IGNORECASE),
    re.compile(r"stake per free spin(?: bet)? is (?P<v>£\s?\d+(?:\.\d+)?|\d+(?:\.\d+)?\s?p)", re.IGNORECASE),
]
_COIN_LINES = re.compile(r"(?P<coin>\d+)p (?:coin size|per (?:line|coin)),?\s*(?:across\s*)?(?P<lines>\d+) lines",
                         re.IGNORECASE)
_SPIN_COUNT = re.compile(r"\b(?P<n>\d{1,4})\s+(?:x\s+£[\d.]+\s+)?(?:free\s?spins|FS)\b", re.IGNORECASE)
_SPIN_COUNT_TYPO = re.compile(r"\b(?=[0-9O]*\d)(?=[0-9O]*O)[0-9O]{2,4}\s+free\s?spins\b", re.IGNORECASE)
_MIN_DEPOSIT = [
    re.compile(r"deposit\s*&\s*(?:wager|bet|stake)\s*(?:min\.?\s*)?(?P<v>£\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"deposit(?:s)?\s*:?\s*(?:of\s*)?(?:at least\s*|min(?:imum)?\.?\s*(?:of\s*)?)?(?P<v>£\d+(?:\.\d+)?)\+?",
               re.IGNORECASE),
    re.compile(r"(?P<v>£\d+(?:\.\d+)?)\+?\s*min(?:imum)?\.?\s*deposit", re.IGNORECASE),
    re.compile(r"min(?:imum)?\.?\s*(?P<v>£\d+(?:\.\d+)?)\s*deposit", re.IGNORECASE),
    re.compile(r"deposit\s*\(min\.?\s*(?P<v>£\d+(?:\.\d+)?)\)", re.IGNORECASE),
    re.compile(r"deposit to the minimum value of (?P<v>£\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"first (?P<v>£\d+(?:\.\d+)?) deposit", re.IGNORECASE),
]
_QUAL_WAGER = [
    re.compile(r"deposit\s*&\s*(?:wager|bet|stake)\s*(?:min\.?\s*)?(?P<v>£\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"\b(?:wager|stake|play|bet|spend)(?:ed|s)?\s*(?:at least\s*|min(?:imum)?\.?\s*(?:total\s*)?)?"
               r"(?P<v>£\d+(?:\.\d+)?)\+?\s*(?:or more\s*)?(?:in cash\s*)?(?:on|in)\b", re.IGNORECASE),
    re.compile(r"\b(?:wager|stake|play|bet|spend)\s*(?P<v>£\d+(?:\.\d+)?)\+?\s*or more\b", re.IGNORECASE),
    re.compile(r"\bmake a wager of (?P<v>£\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"(?P<v>£\d+(?:\.\d+)?)\s*wager on slots?", re.IGNORECASE),
    re.compile(r"\b(?:play|bet) (?P<v>£\d+(?:\.\d+)?)\s*(?:,|&)\s*get\b", re.IGNORECASE),
]
_MAX_WIN = re.compile(r"(?:capped at|max(?:imum)?\.? win(?:nings)?(?: amount)?(?: of| is)?)\s*(?P<v>£\s?\d[\d,.]*)"
                      r"|(?P<w>£\s?\d[\d,.]*)\s*max(?:imum)?\.? win", re.IGNORECASE)
_NO_MAX_WIN = re.compile(r"no caps? on (?:what you win|winnings)|uncapped|no maximum win|no cap on winnings",
                         re.IGNORECASE)
# "use code BETGETCASINO", "the code SNG50", or any case when quoted: "enter the code ‘spin1year’"
_PROMO_CODE = re.compile(r"(?:promo |bonus )?code[:\s]+(?:['‘’\"“](?P<quoted>[A-Za-z0-9]{3,})['‘’\"”]"
                         r"|(?P<code>[A-Z0-9]{3,})\b)")
_NO_CODE = re.compile(r"no (?:promo |bonus )?code (?:is )?(?:needed|required)|promo code\?\s*no\b|no code required",
                      re.IGNORECASE)
_SPIN_GAME = re.compile(r"(?i:free spins)(?: \(.*?\))?(?: of value \S+)?\s*:?\s*(?:on|at|for|in)\s+(?:the\s+)?"
                        r"(?:(?:popular |top |legendary )?(?:slot|game)\s+)?(?P<g>[A-Z][\w'’&!:-]*(?:\s+(?:[A-Z0-9][\w'’&!:-]*|N|of|the|in|x\d+))*)")


_GAME_LABEL = [
    re.compile(r"\bGame:\s*(?P<g>[A-Z][\w'’ ]+?)\s*(?:,|\.|$)"),
    re.compile(r"only eligible for the (?P<g>[A-Z][\w'’ ]+?) slot"),
]
_END_DATE = re.compile(
    r"(?:until|ends?|valid until)\s+(?:[\d.:]+\s*(?:am|pm)?\s*(?:GMT|BST|CET)?\s*on\s+)?(?:\w+day\s+)?"
    r"(?P<d>\d{1,2}(?:st|nd|rd|th)?\s+(?:January|February|March|April|May|June|July|August|September|October"
    r"|November|December)\s+\d{4}|\d{1,2}/\d{1,2}/\d{4})", re.IGNORECASE)
_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                        "september", "october", "november", "december"], start=1)}


def parse_end_date(text: str):
    """"until 30/11/2026" or "until 11.59am GMT on Monday 30 November 2026" → date."""
    from datetime import date as _date
    m = _END_DATE.search(text)
    if not m:
        return None
    raw = m.group("d")
    if "/" in raw:
        day, month, year = (int(x) for x in raw.split("/"))
    else:
        parts = re.sub(r"(st|nd|rd|th)\b", "", raw).split()
        day, month, year = int(parts[0]), _MONTHS[parts[1].lower()], int(parts[2])
    return _date(year, month, day)


def _game_name(raw: str) -> str:
    """Normalise case so "GAME OF THRONES" (rules) and "Game of Thrones" (banner) agree."""
    return " ".join(w if any(ch.isdigit() for ch in w) else w.capitalize() for w in raw.split())


def read_offer_sentence(c: Candidates, s: str, url: str) -> None:
    """Fields expressed in wording shared by most free-spins/deposit offers."""
    low = s.lower()
    if m := _SPIN_COUNT_TYPO.search(s):
        c.typo("free_spins", f"letter O used as a digit: {m.group(0)!r}", url, s)
    for m in _SPIN_COUNT.finditer(s):
        c.add("free_spins", int(m.group("n")), url, s)
    if m := _COIN_LINES.search(s):
        c.add("spin_value", Decimal(m.group("coin")) * Decimal(m.group("lines")) / 100, url, s)
        c.add("currency", "GBP", url, s)
    elif "spin" in low:
        for pattern in _SPIN_VALUE:
            if m := pattern.search(s):
                c.money("spin_value", m.group("v").replace(" ", ""), url, s)
                break
    if "spin" in low and (m := _SPIN_GAME.search(s)):
        game = re.sub(r"\s+(?:will|when|with|and|valued|worth|slot|game|only|for)\b.*$", "", m.group("g"))
        game = game.strip(" .,:!*")
        if game and game.lower() not in ("the", "slots", "slot", "any", "casino") \
                and not re.match(r"(?i)(?:new|sign|real|you|your|every|all|registration|us)\b", game):
            c.add("eligible_games", _game_name(game), url, s)
    for pattern in _GAME_LABEL:
        if m := pattern.search(s):
            c.add("eligible_games", _game_name(m.group("g")), url, s)
    for pattern in _MIN_DEPOSIT:
        if m := pattern.search(s):
            c.money("min_deposit", m.group("v"), url, s)
            break
    for pattern in _QUAL_WAGER:
        if m := pattern.search(s):
            c.money("qualifying_amount", m.group("v"), url, s)
            break
    mult = parse_wagering(s)
    if mult is not None and not re.search(r"contribut", low):
        c.add("wagering_multiplier", mult, url, s)
        if mult and "winnings" in low:
            c.add("wagering_applies_to", "winnings", url, s)
        elif mult and re.search(r"deposit\s*(?:\+|and|&)\s*bonus", low):
            c.add("wagering_applies_to", "deposit+bonus", url, s)
    if m := _MAX_WIN.search(s):
        c.money("max_win", (m.group("v") or m.group("w")).replace(" ", ""), url, s, with_currency=False)
    elif _NO_MAX_WIN.search(s):
        c.none("max_win", url, s)
    if m := _PROMO_CODE.search(s):
        c.add("promo_code", m.group("quoted") or m.group("code"), url, s)
    elif _NO_CODE.search(s):
        c.none("promo_code", url, s)
    if (end := parse_end_date(s)) is not None:
        c.add("offer_end_date", end, url, s)
    read_days(c, s, url)


_TWO_WINDOWS = re.compile(
    r"(?P<a>\d+) days? from (?:when you make your first deposit|the date of registration)[^.]*?to complete"
    r"[^.]*?and (?P<b>\d+) days? after that to play", re.IGNORECASE)


def read_days(c: Candidates, s: str, url: str) -> None:
    """Classify a sentence's duration as claim / use / wagering-expiry time."""
    if m := _TWO_WINDOWS.search(s):
        c.add("claim_days", Decimal(m.group("a")), url, s)
        c.add("use_days", Decimal(m.group("b")), url, s)
        return
    days = parse_days(s)
    if days is None:
        return
    low = s.lower()
    if re.search(r"to (?:claim|qualify|opt in)|\bclaim within|of (?:registration|sign[- ]up|registering)"
                 r"|after (?:registration|sign[- ]up)", low) \
            and not re.search(r"\bexpire|\bto use\b|be used|wager(?:ing)? requirement", low):
        c.add("claim_days", days, url, s)
    elif re.search(r"\bto use\b|be used within|must be used|\bexpire|\bvalid (?:for )?\d|activate", low) \
            and re.search(r"spin|bonus|token|bet", low) \
            and not re.search(r"wager(?:ing)? requirements?\b.*within|must be wagered|if the wager", low):
        c.add("use_days", days, url, s)
    elif re.search(r"wager(?:ing)? requirement|must be wagered|wagered .* within|complete (?:the )?wagering"
                   r"|if the wager is not covered", low):
        c.add("expiry_days", days, url, s)
