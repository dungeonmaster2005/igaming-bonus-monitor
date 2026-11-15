"""Sheet content built from the CI-fetched fixtures: order, metrics, empty-cell states, change log."""

from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from src import tables
from src.config import load_operators
from src.main import read_operator
from src.models import offer_from_dict, offer_to_dict, row_key
from src.state import OperatorRun, State

FIXTURES = Path(__file__).parent / "fixtures"
DAY = date(2026, 11, 15)
D = Decimal


@pytest.fixture(scope="module")
def runs():
    return [read_operator(op, DAY, FIXTURES) for op in load_operators() if op.get("enabled")]


@pytest.fixture()
def today(runs, tmp_path):
    return State(tmp_path).apply(deepcopy(runs), DAY)


def _issues(today):
    return [i for i, _, _ in today.issues]


def _col(table, header):
    return [c.header for c in table.columns].index(header)


def test_every_operator_is_read_from_its_saved_pages(runs):
    assert all(not r.stale for r in runs), [(r.operator_id, r.problems) for r in runs if r.stale]


def test_uk_sorted_by_product_then_reward_per_unit_no_wagering_first(today):
    t = tables.uk_table(today, _issues(today))
    product, operator, rank = _col(t, "Product"), _col(t, "Operator"), _col(t, "Rank")
    products = [r[product].value for r in t.rows]
    assert products == sorted(products, key=lambda p: p != "casino")  # casino block, then sports
    names = [r[operator].value for r in t.rows]
    # 1.00 per £1: the four no-wagering offers share rank 3, Unibet (10x) comes after them
    assert names.index("Unibet") > max(names.index(n) for n in ("Jackpotjoy", "MrQ", "NetBet", "Virgin Games"))
    sports = [r for r in t.rows if r[product].value == "sports"]
    assert [(r[operator].value, r[rank].value) for r in sports] == [("Monopoly Casino", 1)]  # ranked on its own


def test_crypto_sorted_by_turnover_x_deposit(today):
    t = tables.crypto_table(today, _issues(today))
    i = _col(t, "Turnover × deposit")
    values = [r[i].value for r in t.rows]
    numbers = [v for v in values if isinstance(v, Decimal)]
    assert numbers == sorted(numbers) and numbers[0] == D("17.5")  # 7Bit step 3: 50% × 35x
    assert values[-1] == tables.NA  # KatsuBet: wagering on spin winnings


def test_turnover_x_deposit_formula(today):
    offers = {(o.operator_id, o.offer_label): o for o, _, _ in today.rows}
    assert offers[("sevenbit", "Deposit 2 of 4")].turnover_x_deposit == D("26.25")  # 75% × 35
    assert offers[("mbit", "Deposit 3 of 3")].turnover_x_deposit == D(50)          # 125% × 40
    assert offers[("monopoly_casino", "Choice B: free bets")].product == "sports"


def test_four_empty_states_and_links(today):
    issues = _issues(today)
    t = tables.uk_table(today, issues)
    by = {(r[_col(t, "Operator")].value, r[_col(t, "Offer")].value): r for r in t.rows}
    unibet = by[("Unibet", "Welcome offer")]
    use = unibet[_col(t, "Days to use")]
    assert use.value == tables.CONFLICT and use.link == "#issue:unibet_uk:free-spins-welcome-offer/use_days"
    assert by[("MrQ", "Welcome offer")][_col(t, "Max win")].value == tables.NONE
    assert by[("Jackpotjoy", "Welcome offer")][_col(t, "Bonus %")].value == tables.NA
    assert by[("Jackpotjoy", "Welcome offer")][_col(t, "Max bet")].value == tables.MISSING
    spins = by[("Unibet", "Welcome offer")][_col(t, "Free spins")]
    assert spins.value == 300 and spins.link.startswith("https://www.unibet.co.uk/") and spins.note.startswith("“")
    # the issues sheet holds the row every "#issue:" link points at
    anchors = tables.issues_table(today).anchors
    assert use.link in anchors


def test_issues_sorted_by_severity(today):
    t = tables.issues_table(today)
    sev = [r[_col(t, "Severity")].value for r in t.rows]
    assert sev == sorted(sev, key=tables.SEVERITY_ORDER.get) and sev[0] == "high" and sev[-1] == "low"
    assert all(r[_col(t, "Source page")].link for r in t.rows)


def test_header_disclaimer():
    text = tables.DISCLAIMER.format(where="a US IP", day="2026-11-15")
    assert text == ("B2B market data. 18+. Not an offer to gamble. Collected from a US IP on 2026-11-15; "
                    "terms may differ by location.")


def test_no_affiliate_links(today):
    """Links are the operators' own pages (no tracking parameters such as btag/affid)."""
    issues = _issues(today)
    for table in (tables.uk_table(today, issues), tables.crypto_table(today, issues)):
        for row in table.rows:
            for cell in row:
                if cell.link and cell.link.startswith("http"):
                    assert not any(k in cell.link.lower() for k in ("btag", "affid", "aff_id", "affiliate"))


# --- state between runs ---------------------------------------------------------------

def test_offer_round_trips_through_the_state_file(runs):
    offer = runs[0].offers[0]
    again = offer_from_dict(offer_to_dict(offer))
    assert again == offer and again.evidence == offer.evidence


def test_first_run_is_a_baseline_then_changes_are_logged(runs, tmp_path):
    state = State(tmp_path)
    assert state.apply(deepcopy(runs), DAY).changes == []
    state.save()

    later = deepcopy(runs)
    unibet = next(r for r in later if r.operator_id == "unibet_uk")
    unibet.offers[0].free_spins = 250
    unibet.offers[0].terms_text += " New clause."
    unibet.offers[0].terms_hash = ""
    unibet.offers[0].__post_init__()
    mrq = next(r for r in later if r.operator_id == "mrq")
    mrq.offers = []
    result = State(tmp_path).apply(later, date(2026, 11, 16))
    got = {(c.operator_id, c.field, c.was, c.now) for c in result.changes}
    assert ("unibet_uk", "free_spins", "300", "250") in got
    assert any(c.operator_id == "unibet_uk" and c.field == "terms_text" for c in result.changes)
    assert ("mrq", "offer", "listed", "disappeared") in got


def test_stale_operator_is_carried_over_without_changes(runs, tmp_path):
    state = State(tmp_path)
    state.apply(deepcopy(runs), DAY)
    state.save()
    later = deepcopy(runs)
    i = next(n for n, r in enumerate(later) if r.operator_id == "netbet_uk")
    later[i] = OperatorRun("netbet_uk", "NetBet", "UK", 1, 2, problems=["terms: blocked — HTTP 403"])
    state = State(tmp_path)
    result = state.apply(later, date(2026, 11, 16))
    assert not [c for c in result.changes if c.operator_id == "netbet_uk"]
    carried = [(o, seen, stale) for o, seen, stale in result.rows if o.operator_id == "netbet_uk"]
    assert carried and all(stale and seen == "2026-11-15" for _, seen, stale in carried)
    assert state.status["netbet_uk"].stale and state.status["netbet_uk"].last_success == "2026-11-15"
    assert row_key(carried[0][0]) in state.offers  # kept for the next run
