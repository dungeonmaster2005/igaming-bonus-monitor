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


@dataclass
class Passage:
    """One changed passage of the terms: removed and added words with a little context."""

    removed: str
    added: str
    before: str
    after: str
    covered: bool = False   # fully explained by a parsed field that changed (set by the state diff)

    def render(self, max_chars: int = 160) -> str:
        parts = []
        if self.removed:
            parts.append(f"[−{_clip(self.removed, max_chars // 2)}]")
        if self.added:
            parts.append(f"[+{_clip(self.added, max_chars // 2)}]")
        return f"…{self.before} {' '.join(parts)} {self.after}…".replace("… ", "…").replace(" …", "…")


def diff_passages(old: str, new: str, context: int = 6, limit: int = 30) -> list[Passage]:
    """Changed passages, words compared case-insensitively (as for the hash); edits a few words
    apart are merged into one passage. At most `limit` passages are kept."""
    a, b = _display_words(old), _display_words(new)
    matcher = difflib.SequenceMatcher(None, [w.lower() for w in a], [w.lower() for w in b], autojunk=False)
    spans: list[list[int]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if spans and i1 - spans[-1][1] <= 3:
            spans[-1][1], spans[-1][3] = i2, j2
        else:
            spans.append([i1, i2, j1, j2])
    return [Passage(" ".join(a[i1:i2]), " ".join(b[j1:j2]), " ".join(a[max(0, i1 - context):i1]),
                    " ".join(a[i2:i2 + context])) for i1, i2, j1, j2 in spans[:limit]]


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def explained_by(passage: Passage, value_changes: list[tuple[str, str]]) -> bool:
    """True when the passage differs only in numbers, each going from a field's old value to its new one.

    "wagered [−x10] [+x20]" with wagering 10 → 20 is explained; a reworded clause is not.
    """
    if not value_changes or _NUM.sub("#", passage.removed.lower()) != _NUM.sub("#", passage.added.lower()):
        return False
    olds, news = _NUM.findall(passage.removed), _NUM.findall(passage.added)
    pairs = {(_as_number(w), _as_number(n)) for w, n in value_changes}
    changed = [(o, n) for o, n in zip(olds, news) if _as_number(o) != _as_number(n)]
    return bool(changed) and all((_as_number(o), _as_number(n)) in pairs for o, n in changed)


def _as_number(text: str):
    from decimal import Decimal, InvalidOperation
    try:
        return Decimal(text.replace(",", "")).normalize()
    except InvalidOperation:
        return text


def short_diff(old: str, new: str, max_fragments: int = 3, context: int = 6, max_chars: int = 160) -> list[str]:
    """Up to `max_fragments` rendered passages, then "(+N more changed passages)"."""
    return render_passages(diff_passages(old, new, context), max_fragments, max_chars)


def render_passages(passages: list[Passage], max_fragments: int = 3, max_chars: int = 160) -> list[str]:
    out = [p.render(max_chars) for p in passages[:max_fragments]]
    extra = len(passages) - max_fragments
    if extra > 0:
        out.append(f"(+{extra} more changed passage{'' if extra == 1 else 's'})")
    return out


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"
