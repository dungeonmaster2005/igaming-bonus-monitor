"""Unibet UK parser on the offer page as fetched by a GitHub runner (2026-11-15)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from src.config import load_operators
from src.operators.unibet_uk import LayoutChanged, parse

FIXTURE = Path(__file__).parent / "fixtures" / "unibet_uk__offer.html"
OP = next(o for o in load_operators() if o["id"] == "unibet_uk")


@pytest.fixture(scope="module")
def offer():
    offers = parse({"offer": FIXTURE.read_text(encoding="utf-8")}, OP, date(2026, 11, 15))
    assert len(offers) == 1
    return offers[0]


def test_identity(offer):
    assert offer.offer_id == "unibet_uk:free-spins-welcome-offer"
    assert (offer.operator, offer.market, offer.reward_type) == ("Unibet", "UK", "free_spins")


def test_qualifying(offer):
    assert offer.qualifying_action == "deposit+wager"
    assert offer.qualifying_amount == Decimal(30)
    assert offer.min_deposit == Decimal(30)
    assert offer.currency == "GBP"


def test_reward(offer):
    assert offer.free_spins == 300
    assert offer.eligible_games == "Big Bass Splash"


def test_wagering_and_limits(offer):
    assert offer.wagering_multiplier == Decimal(10)
    assert offer.wagering_applies_to == "winnings"
    assert offer.max_bet == Decimal(15)
    assert offer.claim_days == Decimal(7)
    assert offer.expiry_days == Decimal(30)
    assert offer.over_10x is False


def test_ambiguous_and_conflicting_fields_stay_empty(offer):
    # "0.10p" is a tenth of a penny taken literally — not guessed as 10p.
    assert offer.spin_value is None and "ambiguous" in offer.evidence["spin_value"]
    assert offer.total_reward_value is None and offer.reward_per_unit is None
    # The terms give 48 hours, 7 days and 30 days for using the spins.
    assert offer.use_days is None and offer.evidence["use_days"].startswith("conflict")


def test_not_stated_fields_are_empty(offer):
    assert offer.max_win is None and offer.promo_code is None and offer.offer_end_date is None


def test_terms_text_and_hash(offer):
    assert offer.terms_text.startswith("Get 300 FREE SPINS when you play £30 on slots")
    assert "Promotional Terms and Conditions" in offer.terms_text
    assert "Gambling can be addictive" not in offer.terms_text  # footer excluded
    assert len(offer.terms_hash) == 64


def test_missing_terms_block_is_a_layout_change():
    with pytest.raises(LayoutChanged):
        parse({"offer": "<html><h1>Offer</h1></html>"}, OP, date(2026, 11, 15))
