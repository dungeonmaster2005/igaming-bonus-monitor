"""Every parser on the pages a GitHub runner fetched on 2026-11-15 (tests/fixtures)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from src.config import load_operators
from src.operators import PARSERS
from src.operators.base import LayoutChanged

FIXTURES = Path(__file__).parent / "fixtures"
OPS = {o["id"]: o for o in load_operators()}
D = Decimal


def run(op_id):
    op = OPS[op_id]
    pages = {n: (FIXTURES / f"{op_id}__{n}.html").read_text(encoding="utf-8")
             for n in op["pages"] if (FIXTURES / f"{op_id}__{n}.html").exists()}
    return PARSERS[op["parser"]](pages, op, date(2026, 11, 15))


def issue(result, field):
    return next((i for i in result.issues if i.field == field), None)


def test_unibet_uk():
    r = run("unibet_uk")
    (o,) = r.offers
    assert (o.qualifying_action, o.qualifying_amount, o.min_deposit) == ("deposit+wager", D(30), D(30))
    assert (o.free_spins, o.spin_value, o.total_reward_value, o.reward_per_unit) == (300, D("0.10"), D("30.00"), D("1.00"))
    assert (o.wagering_multiplier, o.wagering_applies_to, o.over_10x) == (D(10), "winnings", False)
    assert (o.max_bet, o.claim_days, o.expiry_days, o.use_days) == (D(15), D(7), D(30), None)
    # spin value comes from the lobby banner; "0.10p" on the offer page is reported
    assert o.evidence["spin_value"].source_url == OPS["unibet_uk"]["pages"]["lobby"]
    assert issue(r, "spin_value").type == "ambiguous"
    assert issue(r, "use_days").type == "conflict" and "2, 30, 7" in issue(r, "use_days").detail
    assert "Gambling can be addictive" not in o.terms_text


@pytest.mark.parametrize("op_id, spins, game", [
    ("virgin_games", 100, "Game Of Thrones"),
    ("jackpotjoy", 100, "Fishin Frenzy The Big Catch 2"),
    ("rainbow_riches", 30, "Rainbow Riches Pick N Mix"),
])
def test_gamesys_single_offer(op_id, spins, game):
    (o,) = run(op_id).offers
    assert (o.free_spins, o.spin_value, o.eligible_games) == (spins, D("0.1"), game)
    assert (o.min_deposit, o.qualifying_amount, o.qualifying_action) == (D(10), D(10), "deposit+wager")
    assert (o.wagering_multiplier, o.claim_days, o.use_days) == (D(0), D(30), D(30))


def test_virgin_period_start_and_explicit_nones():
    r = run("virgin_games")
    (o,) = r.offers
    assert issue(r, "claim_days").type == "conflict"  # first deposit (rules) vs registration (offer page)
    assert {"max_win", "promo_code"} <= set(o.stated_none)


def test_monopoly_choice_is_two_rows_with_one_offer_id():
    a, b = run("monopoly_casino").offers
    assert a.offer_id == b.offer_id
    assert (a.reward_type, a.free_spins, a.total_reward_value) == ("free_spins", 30, D("3.0"))
    assert (b.reward_type, b.total_reward_value, b.reward_per_unit) == ("free_bets", D(20), D("2.00"))
    assert b.eligible_games == "sportsbook, odds 1/2 (1.5) or greater"


def test_mrq_typo_is_an_issue():
    r = run("mrq")
    (o,) = r.offers
    assert (o.qualifying_action, o.free_spins, o.spin_value, o.use_days) == ("deposit", 100, D("0.1"), D(2))
    assert o.wagering_multiplier == 0 and "max_win" in o.stated_none
    assert issue(r, "free_spins").type == "typo"


def test_netbet():
    (o,) = run("netbet_uk").offers
    assert (o.free_spins, o.spin_value, o.max_win, o.use_days) == (100, D("0.10"), D(100), D(7))
    assert o.offer_end_date == date(2026, 11, 30) and o.wagering_multiplier == 0


def test_spin_genie_three_way_choice():
    offers = run("spin_genie").offers
    a, b = offers[:2]
    assert len({o.offer_id for o in offers}) == 1
    assert (a.free_spins, a.spin_value, a.wagering_multiplier, a.wagering_applies_to) == (120, D("0.10"), D(10), "winnings")
    assert a.max_bet is None and a.field_notes["max_bet"].startswith("conditional limit")
    assert (b.bonus_percent, b.max_bonus, b.free_spins, b.promo_code) == (D(100), D(25), 50, "SNG50")
    assert b.qualifying_amount == D(25) and b.total_reward_value == D("30.0")
    assert b.wagering_multiplier is None and "Bonus Policy" in b.field_notes["wagering_multiplier"]


def test_midnite():
    (o,) = run("midnite").offers
    assert (o.promo_code, o.min_deposit, o.qualifying_amount, o.claim_days) == ("BETGETCASINO", D(20), D(20), D(14))
    assert (o.free_spins, o.spin_value, o.use_days, o.reward_per_unit) == (100, D("0.1"), D(7), D("0.50"))
    assert o.wagering_multiplier is None  # not mentioned in the article


def test_bitstarz_package():
    offers = run("bitstarz").offers
    assert [o.max_bonus for o in offers] == [D(1), D(1), D(2), D(1)]
    assert [o.required_turnover for o in offers] == [D(40), D(40), D(80), D(40)]
    assert all(o.max_bet == D("0.00006") and o.expiry_days == D(7) for o in offers)
    assert offers[0].free_spins == 180 and offers[0].min_deposit == D("0.0002")


def test_sevenbit_package_and_headline_total():
    r = run("sevenbit")
    assert [o.max_bonus for o in r.offers] == [D("1.5"), D("1.25"), D("1.25"), D(1)]
    assert [o.promo_code for o in r.offers] == [None, "2DEP", "3DEP", "4DEP"]
    assert "promo_code" in r.offers[0].stated_none
    assert all(o.wagering_multiplier == 35 and o.max_bet == D("0.000013") for o in r.offers)
    assert issue(r, "max_bonus (package total)").detail == "headline total 5.25 BTC; sum of the four deposit steps 5.00 BTC"
    assert r.offers[0].license_note.startswith("Site prints company registration number 16498")


def test_mbit_package():
    offers = run("mbit").offers
    assert [(o.bonus_percent, o.max_bonus, o.free_spins) for o in offers] == [
        (D(100), D(1), 100), (D(100), D("1.5"), 100), (D(125), D("1.5"), 125)]
    assert all(o.min_deposit is None and "EUR" in o.field_notes["min_deposit"] for o in offers)
    assert offers[0].required_turnover == D(40)


def test_katsubet():
    (o,) = run("katsubet").offers
    assert (o.min_deposit, o.currency, o.promo_code, o.free_spins) == (D(1), "USD", "1BET", 50)
    assert (o.wagering_multiplier, o.wagering_applies_to) == (D(50), "winnings")


def test_layout_change_is_an_error_not_an_empty_offer():
    op = OPS["unibet_uk"]
    with pytest.raises(LayoutChanged):
        PARSERS["unibet_uk"]({"offer": "<html><h1>Offer</h1></html>"}, op, date(2026, 11, 15))
