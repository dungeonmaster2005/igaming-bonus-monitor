"""Offer record and the metrics derived from it.

One row per reward. When an operator lets the player choose between rewards (e.g.
free spins OR free bets), each choice is its own Offer with a shared `offer_id`.
A field that could not be parsed stays None — parsers never guess.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field, fields
from datetime import date
from decimal import Decimal

UK_WAGERING_CAP = Decimal(10)

QUALIFYING_ACTIONS = ("deposit", "wager", "deposit+wager")
REWARD_TYPES = ("free_spins", "bonus_cash", "free_bets", "cash")
WAGERING_APPLIES_TO = ("bonus", "winnings", "deposit+bonus")


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
    # what the player has to do
    qualifying_action: str | None = None   # deposit / wager / deposit+wager
    qualifying_amount: Decimal | None = None
    min_deposit: Decimal | None = None
    promo_code: str | None = None
    # what the player gets
    reward_type: str | None = None          # free_spins / bonus_cash / free_bets / cash
    currency: str | None = None
    bonus_percent: Decimal | None = None
    max_bonus: Decimal | None = None
    free_spins: int | None = None
    spin_value: Decimal | None = None
    total_reward_value: Decimal | None = None  # spins × spin value, or the bonus amount
    eligible_games: str | None = None
    # restrictions
    wagering_multiplier: Decimal | None = None
    wagering_applies_to: str | None = None  # bonus / winnings / deposit+bonus
    max_bet: Decimal | None = None
    max_win: Decimal | None = None
    claim_days: Decimal | None = None       # time to qualify / claim after registration
    use_days: Decimal | None = None         # time to use the spins / bonus once credited
    expiry_days: Decimal | None = None      # time to complete wagering
    offer_end_date: date | None = None
    # source
    terms_text: str = ""
    terms_hash: str = ""
    # which phrase of the terms each field was read from (for review, not stored in sheets)
    evidence: dict[str, str] = field(default_factory=dict, repr=False)

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
    def over_10x(self) -> bool | None:
        """UK only: wagering above the 10x level (None when not applicable/unknown)."""
        if self.market != "UK" or self.wagering_multiplier is None:
            return None
        return self.wagering_multiplier > UK_WAGERING_CAP

    def as_row(self) -> dict[str, object]:
        """All stored fields plus the derived metrics, without the evidence."""
        row = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "evidence"}
        row.update(reward_per_unit=self.reward_per_unit, required_turnover=self.required_turnover,
                   over_10x=self.over_10x)
        return row


def normalise_terms(text: str) -> str:
    """Whitespace-insensitive form of the terms, so layout changes don't look like edits."""
    return " ".join(text.split())


def terms_hash(text: str) -> str:
    return hashlib.sha256(normalise_terms(text).encode("utf-8")).hexdigest()


__all__ = ["Offer", "normalise_terms", "terms_hash", "asdict"]
