"""Shared parsing of amounts, wagering and durations in bonus terms.

Parsers collect every candidate value for a field together with the sentence it came
from. A field gets a value only when all candidates agree; disagreement (the terms
contradict themselves) or an ambiguous phrase leaves it empty with a note. Never guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

CURRENCY_SYMBOLS = {"£": "GBP", "€": "EUR", "$": "USD"}

_MONEY = re.compile(
    r"(?P<sym>[£€$])\s?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"|(?<![\w.£€$])(?P<pence>\d+(?:\.\d+)?)\s?p\b"
)
_DURATION = re.compile(r"(?P<n>\d+)\s*(?P<unit>hours?|hrs?|days?)\b", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9•(])")


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


def parse_days(text: str) -> Decimal | None:
    """First duration in `text` as days: "within 7 days" → 7, "48 hours" → 2."""
    match = _DURATION.search(text)
    if not match:
        return None
    n = Decimal(match.group("n"))
    return n if match.group("unit").lower().startswith("d") else n / 24


_WAGER = re.compile(
    r"wager(?:ed|ing)?\s+(?:requirement\s+of\s+)?[x×]\s?(?P<a>\d{1,3})\b"
    r"|wager(?:ed|ing)?\s+(?P<b>\d{1,3})\s?(?:x|×|times)\b"
    r"|(?P<c>\d{1,3})\s?[x×]\s+wagering"
    r"|\bWR\s+(?P<d>\d{1,3})\s?x"
    r"|wager(?:ed)?\s+(?:the\s+)?(?:bonus|winnings|deposit)(?:\s*\+\s*bonus)?\s+[x×]\s?(?P<e>\d{1,3})\b",
    re.IGNORECASE,
)


def parse_wagering(text: str) -> Decimal | None:
    """Wagering multiplier: "wagered x10", "wagered 10x", "10x wagering", "WR 10x"."""
    match = _WAGER.search(text)
    if not match:
        return None
    return Decimal(next(g for g in match.groups() if g))


def sentences(text: str) -> list[str]:
    """Split terms into sentences (keeps "£0.10" and "x10." intact)."""
    return [s.strip() for s in _SENTENCE_END.split(" ".join(text.split())) if s.strip()]


@dataclass
class Candidates:
    """Every value seen for each field, with the sentence it came from."""

    values: dict[str, list[tuple[object, str]]] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)

    def add(self, name: str, value: object, source: str) -> None:
        self.values.setdefault(name, []).append((value, source))

    def ambiguous(self, name: str, reason: str, source: str) -> None:
        self.notes[name] = f"ambiguous: {reason} — “{source}”"

    def resolve(self, name: str) -> tuple[object | None, str | None]:
        """(value, evidence) if all candidates agree; (None, note) otherwise."""
        if name in self.notes:
            return None, self.notes[name]
        found = self.values.get(name, [])
        if not found:
            return None, None
        distinct = {value for value, _ in found}
        if len(distinct) == 1:
            return found[0][0], found[0][1]
        unique = dict.fromkeys(found)  # the same sentence can appear twice in the terms
        listing = "; ".join(f"{value} ← “{source}”" for value, source in unique)
        return None, f"conflict: {listing}"
