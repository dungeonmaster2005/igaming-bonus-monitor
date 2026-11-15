"""Alerts and terms normalisation, on the CI-fetched fixtures and an artificial "yesterday"."""

from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from src import alerts
from src.config import load_alert_config, load_operators
from src.main import read_operator
from src.models import Issue, Quote
from src.state import OperatorRun, State
from src.terms_text import normalise, short_diff, terms_hash, volatile_parts

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = load_alert_config()
DAY1, DAY2 = date(2026, 11, 14), date(2026, 11, 15)


@pytest.fixture(scope="module")
def runs():
    return [read_operator(op, DAY1, FIXTURES) for op in load_operators() if op.get("enabled")]


def _two_days(runs, tmp_path, edit):
    state = State(tmp_path)
    first = state.apply(deepcopy(runs), DAY1)
    state.save()
    later = deepcopy(runs)
    edit({r.operator_id: r for r in later})
    state = State(tmp_path)
    second = state.apply(later, DAY2)
    return state, first, second


def _set(offer, **values):
    for k, v in values.items():
        setattr(offer, k, v)
    offer.terms_hash = terms_hash(offer.terms_text)


# --- normalisation --------------------------------------------------------------------

@pytest.mark.parametrize("a, b", [
    ("Terms. Last updated on 1st July 2026. Deposit £10.", "Terms. Last updated on 4th March 2027. Deposit £10."),
    ("Last updated: June 25th 2025 Bonus rules", "last updated: july 2nd 2026 bonus rules"),
    ("© 2026 mBit", "© 2027 mBit"),
    ("min 20 EUR/USD, 1,000 NOK, 0.488 BCH, 2.231 LTC or 0.00154 BTC.",
     "min 20 EUR/USD, 1,000 NOK, 0.481 BCH, 2.233 LTC or 0.00155 BTC."),
    ("Who​s it for?", "Whos it for?"),
    ("Don’t – “quoted”", "Don't - \"quoted\""),
    ("Spin  the\n reels", "spin the reels"),
])
def test_self_changing_parts_do_not_change_the_hash(a, b):
    assert terms_hash(a) == terms_hash(b)


@pytest.mark.parametrize("a, b", [
    ("Offer valid until 30/11/2026.", "Offer valid until 31/12/2026."),   # dates inside the rules count
    ("Deposit £10 and wager £10.", "Deposit £20 and wager £10."),
    ("Up to 1 BTC.", "Up to 2 BTC."),                                     # a lone amount is not an FX list
    ("Promotion ends 23:59 on Monday.", "Promotion ends 11:59 on Monday."),  # times in the rules count
])
def test_real_edits_change_the_hash(a, b):
    assert terms_hash(a) != terms_hash(b)


def test_volatile_parts_lists_what_is_cut():
    cut = volatile_parts("Last updated on 1st July 2026. © 2026 Casino. 1,000 NOK, 0.488 BCH, 2.231 LTC.")
    assert [rule.split(":")[0] for rule, _ in cut] == ["page stamp", "copyright line year", "exchange-rate equivalents"]


def test_short_diff_merges_nearby_edits_and_caps_fragments():
    old = "a b c " + " ".join(f"w{i}" for i in range(60)) + " keep x10 and x10 and x10 and x10 end"
    new = old.replace("x10", "x20")
    frags = short_diff(old, new, max_fragments=3)
    assert len(frags) <= 4 and "[−x10" in frags[0] and "[+x20" in frags[0]
    two = short_diff("limited to 1 bonus per household/computer/IP address. This offer",
                     "limited to 1 bonus per person, household or device. This offer")
    assert len(two) == 1


# --- when a message is sent -------------------------------------------------------------

def test_first_run_is_silent(runs, tmp_path):
    today = State(tmp_path).apply(deepcopy(runs), DAY1)
    assert today.events.first_run and alerts.build_message(today, CONFIG, []) is None


def test_no_changes_no_message(runs, tmp_path):
    _, _, second = _two_days(runs, tmp_path, lambda by_id: None)
    assert second.changes == [] and alerts.build_message(second, CONFIG, []) is None


def test_stale_operator_sends_nothing(runs, tmp_path):
    def edit(by_id):
        by_id["unibet_uk"].offers, by_id["unibet_uk"].issues = [], []
        by_id["unibet_uk"].problems = ["offer: blocked — HTTP 403"]
    _, _, second = _two_days(runs, tmp_path, edit)
    assert alerts.build_message(second, CONFIG, []) is None  # stale ≠ "offer disappeared"


def _edit_demo(by_id):
    unibet = by_id["unibet_uk"].offers[0]
    _set(unibet, wagering_multiplier=Decimal(20),
         terms_text=unibet.terms_text.replace("wagered x10", "wagered x20"))
    netbet = by_id["netbet_uk"].offers[0]
    _set(netbet, terms_text=netbet.terms_text.replace("1 bonus per household", "1 bonus per person"))
    spin = by_id["spin_genie"]
    spin.offers = [o for o in spin.offers if not o.offer_label.startswith("Choice C")]


def test_artificial_yesterday_one_block_per_operator(runs, tmp_path):
    _, _, second = _two_days(runs, tmp_path, _edit_demo)
    msg = alerts.build_message(second, CONFIG, [])
    unibet = msg[msg.index("<b>Unibet</b>"):msg.index("<b>Offers listed")]
    assert "wagering: 10x → <b>20x</b>" in unibet and "flag: wagering 20x is above 10x" in unibet
    assert "terms text updated accordingly" in unibet and "x10" not in unibet  # explained by the field
    netbet = msg[msg.index("<b>NetBet</b>"):msg.index("<b>Unibet</b>")]
    assert "[−household/computer/IP] [+person/computer/IP]" in netbet
    assert msg.index("<b>Offers listed / no longer listed</b>") > msg.index("<b>Unibet</b>")
    assert "Choice C: free spins for a year</a> — no longer listed" in msg
    assert "hash" not in msg
    assert msg.rstrip().endswith("<i>B2B market data. 18+. Not an offer to gamble.</i>")


def test_same_change_on_all_deposit_steps_is_one_line(runs, tmp_path):
    def edit(by_id):
        for o in by_id["sevenbit"].offers:
            o.wagering_multiplier = Decimal(40)
    _, _, second = _two_days(runs, tmp_path, edit)
    msg = alerts.build_message(second, CONFIG, [])
    assert msg.count("wagering: 35x → <b>40x</b>") == 1 and "all 4 steps: wagering" in msg


def test_noisy_source_after_three_days(runs, tmp_path):
    state = State(tmp_path).configure(CONFIG)
    state.apply(deepcopy(runs), DAY1)
    state.save()
    messages = []
    for n, day in enumerate((date(2026, 11, 15), date(2026, 11, 16), date(2026, 11, 17), date(2026, 11, 18))):
        later = deepcopy(runs)
        o = next(r for r in later if r.operator_id == "mbit")
        for offer in o.offers:
            offer.terms_text += f" Rotating banner {n}."
            if n == 3:
                offer.max_bet = Decimal("0.004")  # field alerts stay for a noisy source
        state = State(tmp_path).configure(CONFIG)
        today = state.apply(later, day)
        state.save()
        messages.append(alerts.build_message(today, CONFIG, []))
    assert "Rotating" in messages[0] and "Rotating" in messages[1]
    assert "Rotating" not in messages[2] and "changed on 3 consecutive days (2026-11-15, 2026-11-16, 2026-11-17)"         in messages[2]
    assert "Rotating" not in messages[3] and "consecutive" not in messages[3]   # told once
    assert "max bet: 0.003 BTC → <b>0.004 BTC</b>" in messages[3]
    assert State(tmp_path).noise["mbit"]["noisy_since"] == "2026-11-17"


def test_noisy_shown_on_status_sheet():
    from src import tables
    from src.state import SiteStatus
    t = tables.status_table({"mbit": SiteStatus("mbit", "mBit Casino", "crypto")}, [],
                            {"mbit": {"noisy_since": "2026-11-17",
                                      "text_change_days": ["2026-11-15", "2026-11-16", "2026-11-17"]}})
    cell = t.rows[0][[c.header for c in t.columns].index("Terms text")]
    assert cell.value == "NOISY since 2026-11-17 (changed on 2026-11-15, 2026-11-16, 2026-11-17)"


def test_issue_opened_and_closed(runs, tmp_path):
    def edit(by_id):
        by_id["mrq"].issues = []  # the typo was fixed
        by_id["katsubet"].issues = [Issue(DAY2, "katsubet", "KatsuBet", "katsubet:1bet", "max_bet", "conflict",
                                          [Quote("https://www.katsubet.com/bonus-terms", "max bet 5 USD"),
                                           Quote("https://www.katsubet.com/bonus-terms", "max bet 4 USD")],
                                          "values found: 5, 4")]
    _, _, second = _two_days(runs, tmp_path, edit)
    msg = alerts.build_message(second, CONFIG, [])
    assert "flag: new issue: max_bet, conflict (high): values found: 5, 4" in msg
    assert "<b>MrQ</b>" in msg and "issue resolved: free_spins, typo" in msg


def test_offer_ending_alerted_once(runs, tmp_path):
    state = State(tmp_path)
    state.apply(deepcopy(runs), DAY1)
    state.save()
    near = date(2026, 11, 24)  # NetBet ends 2026-11-30
    later = deepcopy(runs)
    state = State(tmp_path)
    today = state.apply(later, near)
    ending = state.offers_ending(today, CONFIG["offer_ending_days"])
    assert [(o.operator_id, left) for o, left in ending] == [("netbet_uk", 6)]
    assert "flag: offer ends 2026-11-30 (in 6 days)" in alerts.build_message(today, CONFIG, ending)
    state.save()
    again = State(tmp_path)
    assert again.offers_ending(again.apply(deepcopy(runs), date(2026, 11, 25)), 7) == []


def test_group_limit_and_more(runs, tmp_path):
    config = {**CONFIG, "max_lines_per_operator": 2}

    def edit(by_id):
        o = by_id["netbet_uk"].offers[0]
        o.free_spins, o.max_win, o.use_days, o.spin_value = 120, Decimal(150), Decimal(10), Decimal("0.2")
    _, _, second = _two_days(runs, tmp_path, edit)
    msg = alerts.build_message(second, config, [])
    assert "…and 2 more" in msg


def test_neutral_wording():
    """No calls to action in the message templates."""
    text = " ".join(alerts.FIELD_LABELS.values()).lower() + CONFIG["caption"].lower()
    assert not any(w in text for w in ("claim now", "sign up", "play now", "don't miss", "hurry", "bonus code"))
