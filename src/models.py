"""Offer and Issue records, and the metrics derived from an offer.

One Offer row per reward. When an operator lets the player choose between rewards (e.g.
free spins OR free bets), each choice is its own Offer with a shared `offer_id`.
A field that could not be read unambiguously stays None — parsers never guess.
Every filled field keeps the official page it was read from and the verbatim quote.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, fields
from datetime import date
from decimal import Decimal

UK_WAGERING_CAP = Decimal(10)

QUALIFYING_ACTIONS = ("deposit", "wager", "deposit+wager")
REWARD_TYPES = ("free_spins", "bonus_cash", "free_bets", "cash")
WAGERING_APPLIES_TO = ("bonus", "winnings", "deposit+bonus")
ISSUE_TYPES = ("conflict", "ambiguous", "typo")
ISSUE_SEVERITY = {"conflict": "high", "ambiguous": "medium", "typo": "low"}
PRODUCTS = ("casino", "sports")  # rankings and comparisons stay within one product

# Fields filled by parsers from the terms (as opposed to identity/config fields).
PARSED_FIELDS = (
    "qualifying_action", "qualifying_amount", "min_deposit", "promo_code",
    "reward_type", "currency", "bonus_percent", "max_bonus", "free_spins", "spin_value",
    "total_reward_value", "eligible_games",
    "wagering_multiplier", "wagering_applies_to", "max_bet", "max_win",
    "claim_days", "use_days", "expiry_days", "offer_end_date",
)


@dataclass(frozen=True)
class Quote:
    """A verbatim phrase and the official page it appears on."""

    source_url: str
    text: str


@dataclass
class Issue:
    """A fact about the terms themselves: they contradict or can be read two ways."""

    date: date
    operator_id: str
    operator: str
    offer_id: str
    field: str
    type: str          # conflict / ambiguous / typo
    quotes: list[Quote]
    detail: str = ""   # neutral, factual (e.g. "values found: 2, 7, 30 days")

    @property
    def severity(self) -> str:
        return ISSUE_SEVERITY[self.type]

    @property
    def source_urls(self) -> list[str]:
        return list(dict.fromkeys(q.source_url for q in self.quotes))


@dataclass
class Offer:
    # identity
    date: date
    operator_id: str
    operator: str
    market: str
    license: str
    url: str
    offer_id: str
    offer_type: str = "casino_welcome"
    product: str = "casino"                  # casino / sports (e.g. a free-bet alternative)
    license_note: str | None = None
    offer_label: str | None = None           # e.g. "Choice A: free spins" for alternatives
    # what the player has to do
    qualifying_action: str | None = None     # deposit / wager / deposit+wager
    qualifying_amount: Decimal | None = None
    min_deposit: Decimal | None = None
    promo_code: str | None = None
    # what the player gets
    reward_type: str | None = None           # free_spins / bonus_cash / free_bets / cash
    currency: str | None = None
    bonus_percent: Decimal | None = None
    max_bonus: Decimal | None = None
    free_spins: int | None = None
    spin_value: Decimal | None = None
    total_reward_value: Decimal | None = None  # spins × spin value, or the bonus amount
    eligible_games: str | None = None
    # restrictions
    wagering_multiplier: Decimal | None = None
    wagering_applies_to: str | None = None   # bonus / winnings / deposit+bonus
    max_bet: Decimal | None = None
    max_win: Decimal | None = None
    claim_days: Decimal | None = None        # time to qualify / claim after registration
    use_days: Decimal | None = None          # time to use the spins / bonus once credited
    expiry_days: Decimal | None = None       # time to complete wagering
    offer_end_date: date | None = None
    # source
    terms_text: str = ""
    terms_hash: str = ""
    evidence: dict[str, Quote] = field(default_factory=dict, repr=False)
    # fields the terms explicitly say do not apply ("no cap on winnings", "no code needed")
    stated_none: dict[str, Quote] = field(default_factory=dict, repr=False)
    # why a field is empty when that is not an issue (e.g. a conditional limit)
    field_notes: dict[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.terms_text and not self.terms_hash:
            self.terms_hash = terms_hash(self.terms_text)

    # -- derived metrics ------------------------------------------------------

    @property
    def reward_per_unit(self) -> Decimal | None:
        """Reward value per unit the player must deposit/wager to qualify (UK metric)."""
        if self.total_reward_value is None or not self.qualifying_amount:
            return None
        return (self.total_reward_value / self.qualifying_amount).quantize(Decimal("0.01"))

    @property
    def required_turnover(self) -> Decimal | None:
        """Wagering needed to release the full bonus (crypto metric).

        Bonus amount × multiplier, plus the matching deposit when wagering applies to
        deposit+bonus. Unknown (None) when wagering applies to winnings, because those
        depend on how the spins land.
        """
        if self.wagering_multiplier is None or self.max_bonus is None:
            return None
        if self.wagering_applies_to == "bonus":
            return self.max_bonus * self.wagering_multiplier
        if self.wagering_applies_to == "deposit+bonus" and self.bonus_percent:
            deposit = self.max_bonus * 100 / self.bonus_percent
            return (deposit + self.max_bonus) * self.wagering_multiplier
        return None

    @property
    def turnover_x_deposit(self) -> Decimal | None:
        """Crypto metric: wagering needed per unit deposited (independent of deposit size).

        bonus_percent / 100 × multiplier when wagering applies to the bonus; with
        deposit+bonus the deposit itself is wagered too: (1 + bonus_percent / 100) × multiplier.
        Unknown when wagering applies to winnings or the terms do not give the base.
        """
        if self.wagering_multiplier is None or self.bonus_percent is None:
            return None
        share = self.bonus_percent / 100
        if self.wagering_applies_to == "bonus":
            return share * self.wagering_multiplier
        if self.wagering_applies_to == "deposit+bonus":
            return (1 + share) * self.wagering_multiplier
        return None

    @property
    def over_10x(self) -> bool | None:
        """UK only: wagering above the 10x level (None when not applicable/unknown)."""
        if self.market != "UK" or self.wagering_multiplier is None:
            return None
        return self.wagering_multiplier > UK_WAGERING_CAP

    def derive_totals(self) -> None:
        """Fill total_reward_value from its parts when the parser did not set it."""
        if self.total_reward_value is not None:
            return
        spins_value = None
        if self.free_spins is not None and self.spin_value is not None:
            spins_value = self.free_spins * self.spin_value
        if self.reward_type == "free_spins":
            self.total_reward_value = spins_value
        elif self.reward_type in ("bonus_cash", "cash", "free_bets") and self.max_bonus is not None:
            if self.free_spins is None:
                self.total_reward_value = self.max_bonus
            elif spins_value is not None:  # bonus + spins package; unknown if the spins have no value
                self.total_reward_value = self.max_bonus + spins_value

    def as_row(self) -> dict[str, object]:
        """All stored fields plus the derived metrics, without the evidence."""
        hidden = ("evidence", "stated_none", "field_notes")
        row = {f.name: getattr(self, f.name) for f in fields(self) if f.name not in hidden}
        row.update(reward_per_unit=self.reward_per_unit, required_turnover=self.required_turnover,
                   turnover_x_deposit=self.turnover_x_deposit, over_10x=self.over_10x)
        return row


@dataclass
class ParseResult:
    offers: list[Offer]
    issues: list[Issue] = field(default_factory=list)


def normalise_terms(text: str) -> str:
    """Whitespace-insensitive form of the terms, so layout changes don't look like edits."""
    return " ".join(text.split())


def terms_hash(text: str) -> str:
    return hashlib.sha256(normalise_terms(text).encode("utf-8")).hexdigest()


# --- (de)serialisation for the state cache (data/offers_latest.json) ------------------

def _plain(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


_DECIMAL_FIELDS = {f.name for f in fields(Offer) if "Decimal" in str(f.type)}
_DATE_FIELDS = {"date", "offer_end_date"}


def offer_to_dict(offer: Offer) -> dict:
    data = {f.name: _plain(getattr(offer, f.name)) for f in fields(Offer)
            if f.name not in ("evidence", "stated_none", "field_notes")}
    data["evidence"] = {k: [q.source_url, q.text] for k, q in offer.evidence.items()}
    data["stated_none"] = {k: [q.source_url, q.text] for k, q in offer.stated_none.items()}
    data["field_notes"] = dict(offer.field_notes)
    return data


def offer_from_dict(data: dict) -> Offer:
    data = dict(data)
    evidence = {k: Quote(*v) for k, v in data.pop("evidence", {}).items()}
    stated_none = {k: Quote(*v) for k, v in data.pop("stated_none", {}).items()}
    notes = data.pop("field_notes", {})
    known = {f.name for f in fields(Offer)}
    for key in list(data):
        if key not in known:
            data.pop(key)
        elif data[key] is not None and key in _DECIMAL_FIELDS:
            data[key] = Decimal(data[key])
        elif data[key] is not None and key in _DATE_FIELDS:
            data[key] = date.fromisoformat(data[key])
    return Offer(**data, evidence=evidence, stated_none=stated_none, field_notes=notes)


def issue_to_dict(issue: Issue) -> dict:
    return {"date": issue.date.isoformat(), "operator_id": issue.operator_id, "operator": issue.operator,
            "offer_id": issue.offer_id, "field": issue.field, "type": issue.type, "detail": issue.detail,
            "quotes": [[q.source_url, q.text] for q in issue.quotes]}


def issue_from_dict(data: dict) -> Issue:
    return Issue(date=date.fromisoformat(data["date"]), operator_id=data["operator_id"], operator=data["operator"],
                 offer_id=data["offer_id"], field=data["field"], type=data["type"], detail=data.get("detail", ""),
                 quotes=[Quote(*q) for q in data["quotes"]])


def row_key(offer: Offer) -> str:
    """Stable id of one row: the offer plus its choice/step ("Choice B", "Deposit 2 of 4")."""
    label = (offer.offer_label or "").split(":")[0].strip()
    return f"{offer.offer_id}#{label}" if label else offer.offer_id


def issue_id(issue: Issue) -> str:
    return f"{issue.offer_id}/{issue.field}"
