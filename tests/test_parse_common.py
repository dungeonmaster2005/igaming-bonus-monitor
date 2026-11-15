"""Amount, wagering and duration parsing on wording taken from real offer pages."""

from decimal import Decimal

import pytest

from src.parse_common import Ambiguous, Candidates, Money, parse_days, parse_money, parse_wagering, sentences


@pytest.mark.parametrize("text, expected", [
    ("Deposit £30+ via debit card or instant bank transfer only.", Money(Decimal(30), "GBP")),
    ("300 x £0.10 Free Spins on Big Bass Splash", Money(Decimal("0.10"), "GBP")),
    ("Get 100% up to $500 Bonus + 100 Free spins", Money(Decimal(500), "USD")),
    ("max bonus is €1,000", Money(Decimal(1000), "EUR")),
    ("Play £10, Get 100 Free Spins. 10p per spin.", Money(Decimal(10), "GBP")),
    ("Free spins worth 10p each", Money(Decimal("0.10"), "GBP")),
])
def test_parse_money(text, expected):
    assert parse_money(text) == expected


def test_decimal_pence_is_ambiguous():
    # Unibet UK: "300 Free Spins of value 0.10p" — a tenth of a penny, or 10p?
    with pytest.raises(Ambiguous):
        parse_money("300 Free Spins of value 0.10p at Big Bass Splash")


def test_no_money():
    assert parse_money("New customers only") is None


@pytest.mark.parametrize("text, expected", [
    ("Any winnings must be wagered x10 before any winnings become withdrawable.", 10),
    ("Bonus funds must be wagered 10x on slot games before becoming withdrawable.", 10),
    ("Bets placed on the following slot games will not contribute to the 10x wagering requirement.", 10),
    ("Wager Bonus x30 • Full T&Cs apply", 30),
    ("Bonus must be wagered 30 times within 90 days from granting.", 30),
    ("WR 10x free spin winnings (Slots only).", 10),
])
def test_parse_wagering(text, expected):
    assert parse_wagering(text) == Decimal(expected)


def test_wagering_absent():
    assert parse_wagering("Spins expire within 48 hours") is None


@pytest.mark.parametrize("text, expected", [
    ("Opt in and play within 7 days of registration to claim your free spins.", Decimal(7)),
    ("Once claimed, you have 48 hours to use the spins before they expire.", Decimal(2)),
    ("Free spins will expire after 30 days of claiming.", Decimal(30)),
    ("No time limit mentioned here.", None),
])
def test_parse_days(text, expected):
    assert parse_days(text) == expected


def test_sentences_keep_decimals_and_multipliers():
    text = "Stake £30 on Slots. Spins of value £0.10 each. Winnings wagered x10. Done"
    assert sentences(text) == ["Stake £30 on Slots.", "Spins of value £0.10 each.", "Winnings wagered x10.", "Done"]


def test_candidates_agree_or_conflict():
    c = Candidates()
    c.add("claim_days", Decimal(7), "Play within 7 days of registration to claim.")
    c.add("claim_days", Decimal(7), "Opt in within 7 days to claim.")
    c.add("use_days", Decimal(2), "You have 48 hours to use the spins.")
    c.add("use_days", Decimal(7), "Players have 7 days to use their free spins.")
    assert c.resolve("claim_days")[0] == Decimal(7)
    value, note = c.resolve("use_days")
    assert value is None and note.startswith("conflict")
    assert c.resolve("max_win") == (None, None)
