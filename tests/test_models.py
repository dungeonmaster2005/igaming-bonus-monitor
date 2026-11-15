"""Derived metrics: reward_per_unit (UK), required_turnover (crypto), over_10x (UK)."""

from datetime import date
from decimal import Decimal

from src.models import Offer, terms_hash


def _offer(**kw):
    base = dict(date=date(2026, 11, 15), operator_id="x", operator="X", market="UK", license="UKGC",
                url="https://x.example/offer", offer_id="x:welcome")
    return Offer(**{**base, **kw})


def test_reward_per_unit():
    offer = _offer(free_spins=100, spin_value=Decimal("0.10"), total_reward_value=Decimal(10),
                   qualifying_amount=Decimal(10))
    assert offer.reward_per_unit == Decimal("1.00")


def test_reward_per_unit_unknown_without_value():
    assert _offer(qualifying_amount=Decimal(30)).reward_per_unit is None


def test_required_turnover_on_bonus():
    # 100% up to 5 BTC-equivalent €500, 40x the bonus
    offer = _offer(market="crypto", max_bonus=Decimal(500), wagering_multiplier=Decimal(40),
                   wagering_applies_to="bonus")
    assert offer.required_turnover == Decimal(20000)


def test_required_turnover_on_deposit_plus_bonus():
    # 100% up to 200, 35x (deposit + bonus): (200 + 200) × 35
    offer = _offer(market="crypto", bonus_percent=Decimal(100), max_bonus=Decimal(200),
                   wagering_multiplier=Decimal(35), wagering_applies_to="deposit+bonus")
    assert offer.required_turnover == Decimal(14000)


def test_required_turnover_unknown_for_winnings():
    offer = _offer(max_bonus=Decimal(30), wagering_multiplier=Decimal(10), wagering_applies_to="winnings")
    assert offer.required_turnover is None


def test_over_10x_only_for_uk():
    assert _offer(wagering_multiplier=Decimal(30)).over_10x is True
    assert _offer(wagering_multiplier=Decimal(10)).over_10x is False
    assert _offer(market="crypto", wagering_multiplier=Decimal(40)).over_10x is None
    assert _offer().over_10x is None


def test_terms_hash_ignores_whitespace_only_changes():
    assert terms_hash("Deposit £10.\n\n  Get 100 spins.") == terms_hash("Deposit £10. Get 100 spins.")
    assert terms_hash("Deposit £10.") != terms_hash("Deposit £20.")


def test_hash_filled_from_terms_text():
    assert _offer(terms_text="abc").terms_hash == terms_hash("abc")
