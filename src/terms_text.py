"""Terms text before hashing: normalisation, volatile parts and a short readable diff.

A terms text only counts as changed when its wording changed. Parts that change by
themselves (page stamps, clocks, counters, invisible characters, typography) are cut
before hashing; each rule below says what it cuts. `volatile_parts()` lists what was cut
from a given text, so the rules can be checked against real pages.

Not cut on purpose: dates and amounts inside the rules themselves ("until 30/11/2026",
"£10"), because those are exactly the changes the monitor exists to report.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass

_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE = (rf"(?:\d{{1,2}}(?:st|nd|rd|th)?[ ./-]+(?:{_MONTHS}|\d{{1,2}})[ ./-]+\d{{2,4}}"
         rf"|{_MONTHS} \d{{1,2}}(?:st|nd|rd|th)?,? \d{{4}}|\d{{4}}-\d{{2}}-\d{{2}})")


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern
    replacement: object = " "   # str, or a function of the match


_CODES = r"(?:btc|bch|ltc|eth|doge|usdt|usdc|trx|xrp|ada|bnb|sol|nok|sek|jpy|eur|usd|cad|aud|nzd|mbtc)"
_AMOUNT = rf"\d[\d,]*(?:\.\d+)?\s?(?:{_CODES}(?:/{_CODES})*)\b"


def _hash_digits(m: re.Match) -> str:
    return re.sub(r"\d[\d,]*(?:\.\d+)?", "#", m.group(0))


# Applied in order, to text that is already lower-cased. Every rule was found on a monitored page
# (see the phase 4 notes in CLAUDE.md); rules are not added "just in case", because each one also
# hides whatever real change could happen inside the text it cuts.
RULES = (
    Rule("page stamp: last updated / modified / reviewed + date",
         re.compile(rf"\b(?:last\s+)?(?:updated|modified|reviewed|revised|amended)(?:\s+on)?\s*:?\s*{_DATE}")),
    Rule("copyright line year", re.compile(r"(?:©|\(c\)|copyright)\s*(?:\d{4}\s*[-–]\s*)?\d{4}")),
    Rule("exchange-rate equivalents: a list of 3+ amounts in different coins/currencies → digits as #",
         re.compile(rf"{_AMOUNT}(?:\s*(?:,|or)\s*{_AMOUNT}){{2,}}"), _hash_digits),
    Rule("invisible characters (zero-width, BOM, soft hyphen)",
         re.compile("[​‌‍⁠﻿­]"), ""),
    Rule("typographic quotes and dashes → plain", re.compile("[‘’‚′`]"), "'"),
    Rule("typographic double quotes → plain", re.compile("[“”„″]"), '"'),
    Rule("en/em dash, minus → hyphen", re.compile("[–—−]"), "-"),
    Rule("non-breaking and other unicode spaces", re.compile(r"[  -   　]")),
)


def normalise(text: str) -> str:
    """Lower case, volatile parts cut, whitespace collapsed: the form that is hashed."""
    out = text.lower()
    for rule in RULES:
        out = rule.pattern.sub(rule.replacement, out)
    return " ".join(out.split())


def terms_hash(text: str) -> str:
    return hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()


def volatile_parts(text: str) -> list[tuple[str, str]]:
    """(rule, matched text) for every volatile part found — what normalise() cuts."""
    found, out = [], text.lower()
    for rule in RULES:
        if rule.replacement in ("'", '"', "-"):  # typography: replaced, not cut
            continue
        found += [(rule.name, m.group(0)) for m in rule.pattern.finditer(out) if m.group(0).strip()]
        out = rule.pattern.sub(rule.replacement, out)
    return found


def _display_words(text: str) -> list[str]:
    """Words of the original text (case kept) with volatile parts cut, for a readable diff."""
    out = text
    for rule in RULES:
        out = re.compile(rule.pattern.pattern, re.IGNORECASE).sub(rule.replacement, out)
    return out.split()


def short_diff(old: str, new: str, max_fragments: int = 3, context: int = 6, max_chars: int = 160) -> list[str]:
    """Up to `max_fragments` changed passages: "…context [−removed] [+added] context…".

    Words are compared case-insensitively, as for the hash; the original casing is shown.
    """
    a, b = _display_words(old), _display_words(new)
    matcher = difflib.SequenceMatcher(None, [w.lower() for w in a], [w.lower() for w in b], autojunk=False)
    # edits a few words apart read as one passage
    spans: list[list[int]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if spans and i1 - spans[-1][1] <= 3:
            spans[-1][1], spans[-1][3] = i2, j2
        else:
            spans.append([i1, i2, j1, j2])
    fragments = []
    for i1, i2, j1, j2 in spans:
        if len(fragments) == max_fragments:
            fragments.append(f"(+{len(spans) - max_fragments} more changed passage"
                             f"{'' if len(spans) - max_fragments == 1 else 's'})")
            break
        before = " ".join(a[max(0, i1 - context):i1])
        after = " ".join(a[i2:i2 + context])
        removed, added = " ".join(a[i1:i2]), " ".join(b[j1:j2])
        parts = []
        if removed:
            parts.append(f"[−{_clip(removed, max_chars // 2)}]")
        if added:
            parts.append(f"[+{_clip(added, max_chars // 2)}]")
        fragments.append(f"…{before} {' '.join(parts)} {after}…".replace("… ", "…").replace(" …", "…"))
    return fragments


def count_changes(old: str, new: str) -> int:
    a, b = [w.lower() for w in _display_words(old)], [w.lower() for w in _display_words(new)]
    return sum(1 for op in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op[0] != "equal")


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"
