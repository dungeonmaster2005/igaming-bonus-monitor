"""Shared parsing on wording taken from the operators' own pages (fetched 2026-11-15)."""

from datetime import date
from decimal import Decimal

import pytest

from src.parse_common import (
    STATED_NONE, Ambiguous, Candidates, Money, parse_crypto_amount, parse_days, parse_end_date,
    parse_money, parse_wagering, read_offer_sentence, sentences,
)

URL = "https://operator.example/offer"


@pytest.mark.parametrize("text, expected", [
    ("Deposit £30+ via debit card or instant bank transfer only.", Money(Decimal(30), "GBP")),
    ("300 x £0.10 Free Spins on Big Bass Splash", Money(Decimal("0.10"), "GBP")),
    ("Get 100% up to $500 Bonus + 100 Free spins", Money(Decimal(500), "USD")),
    ("max bonus is €1,000", Money(Decimal(1000), "EUR")),
    ("Free spins worth 10p each", Money(Decimal("0.10"), "GBP")),
])
def test_parse_money(text, expected):
    assert parse_money(text) == expected


def test_decimal_pence_is_ambiguous():
    with pytest.raises(Ambiguous):  # Unibet UK: "300 Free Spins of value 0.10p"
        parse_money("300 Free Spins of value 0.10p at Big Bass Splash")


@pytest.mark.parametrize("text, expected", [
    ("a) First deposit bonus: 100% up to 1.5 BTC + 100 free spins.", Money(Decimal("1.5"), "BTC")),
    ("maximum bet per spin ... is 13 μBTC.", Money(Decimal("0.000013"), "BTC")),
    ("minimum deposit ... is 0.26 mBTC.", Money(Decimal("0.00026"), "BTC")),
    ("make your first deposit of 30 EUR or crypto equivalent", Money(Decimal(30), "EUR")),
])
def test_parse_crypto_amount(text, expected):
    assert parse_crypto_amount(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("Any winnings must be wagered x10 before any winnings become withdrawable.", 10),
    ("Bonus funds must be wagered 10x on slot games before becoming withdrawable.", 10),
    ("Wager Bonus x30 • Full T&Cs apply", 30),
    ("Bonus must be wagered 30 times within 90 days from granting.", 30),
    ("WR 10x free spin winnings (Slots only).", 10),
    ("All bonuses (unless otherwise specified) need to be wagered 35 times before the funds can be withdrawn.", 35),
    ("50 Wager for FS", 50),
    ("No wagering on your winnings, no caps on what you win, no fees on withdrawals.", 0),
    ("All Free Spins winnings will be credited as cash, without wagering requirements.", 0),
    ("c) have no wagering requirements and there is no cash alternative to Free Spins.", 0),
])
def test_parse_wagering(text, expected):
    assert parse_wagering(text) == Decimal(expected)


@pytest.mark.parametrize("text, expected", [
    ("Opt in and play within 7 days of registration to claim your free spins.", Decimal(7)),
    ("Once claimed, you have 48 hours to use the spins before they expire.", Decimal(2)),
    ("No time limit mentioned here.", None),
])
def test_parse_days(text, expected):
    assert parse_days(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("Offer valid from 15/10/2026 until 30/11/2026 and limited to 1 bonus per household.", date(2026, 11, 30)),
    ("All new customers from 10am BST on Thursday 15 October 2026 until 11.59am GMT on Monday 30 November 2026.",
     date(2026, 11, 30)),
    ("New customers only, ends 30/11.", None),  # no year — not guessed
])
def test_parse_end_date(text, expected):
    assert parse_end_date(text) == expected


def test_sentences_keep_decimals_and_question_answers():
    assert sentences("Stake £30 on Slots. Spins of value £0.10 each. Winnings wagered x10. Done") == [
        "Stake £30 on Slots.", "Spins of value £0.10 each.", "Winnings wagered x10.", "Done"]
    assert sentences("Welcome Bonus Promo Code? No. Opt in only") == ["Welcome Bonus Promo Code? No.", "Opt in only"]


def _read(*texts):
    c = Candidates()
    for t in texts:
        read_offer_sentence(c, t, URL)
    return c


def test_coin_size_times_lines_is_the_spin_value():
    c = _read("receive 100 free spins (1p coin size, 10 lines) for the game GAME OF THRONES.")
    assert c.resolve("spin_value").value == Decimal("0.1")
    assert c.resolve("free_spins").value == 100
    assert c.resolve("eligible_games").value == "Game Of Thrones"


def test_qualifying_deposit_and_wager():
    c = _read("c) make a first deposit of at least £10; and d) wager at least £10 in cash on any slot game(s).")
    assert c.resolve("min_deposit").value == Decimal(10)
    assert c.resolve("qualifying_amount").value == Decimal(10)


def test_max_win_capped_or_explicitly_uncapped():
    assert _read("The maximum amount of cash winnings derived from the Free Spins is capped at £100.") \
        .resolve("max_win").value == Decimal(100)
    assert _read("All winnings are uncapped and credited to your real money balance.") \
        .resolve("max_win").value == STATED_NONE


def test_promo_code_or_no_code():
    assert _read("Sign up : United Kingdom : use code BETGETCASINO").resolve("promo_code").value == "BETGETCASINO"
    assert _read("No code is needed to enjoy 120 free spins.").resolve("promo_code").value == STATED_NONE


def test_letter_o_typo_is_reported_and_the_clear_value_used():
    c = _read("1OO Free Spins", "Qualifying customers will receive 100 free spins on Big Bass Splash game.")
    res = c.resolve("free_spins")
    assert res.value == 100 and res.issue_type == "typo"


def test_ambiguous_phrase_does_not_block_the_unambiguous_one():
    c = _read("300 Free Spins of value 0.10p at Big Bass Splash will be credited.",
              "Opt in, Deposit & Stake £30 on Slots to get 300 x £0.10 Free Spins on Big Bass Splash, 10x wagering.")
    res = c.resolve("spin_value")
    assert res.value == Decimal("0.10") and res.issue_type == "ambiguous"
    assert res.evidence.text.startswith("Opt in, Deposit & Stake £30")


def test_disagreeing_clear_values_are_a_conflict():
    c = _read("Once claimed, you have 48 hours to use the spins before they expire.",
              "Players have 7 days to use their free spins.")
    res = c.resolve("use_days")
    assert res.value is None and res.issue_type == "conflict" and len(res.issue_quotes) == 2


def test_two_windows_in_one_sentence():
    c = _read("You have 30 days from when you make your first deposit to complete the remaining Qualifying "
              "Requirements and 30 days after that to play any Free Spins before they expire.")
    assert c.resolve("claim_days").value == Decimal(30) and c.resolve("use_days").value == Decimal(30)
