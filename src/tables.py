"""What every sheet shows: columns, row order and the content of each cell.

Kept apart from the Google API code (src/sheets.py) so it can be tested offline.

Empty cells have four distinct states:
  "none"     the terms explicitly say there is no such limit/code
  "n/a"      the field does not apply to this reward (e.g. wagering on free bets)
  "—"        not stated on the pages read
  "conflict" the pages contradict each other; the cell links to the row in "issues"
Every value read from the terms links to its source page; the cell note holds the quote.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from src.models import ISSUE_SEVERITY, Issue, Offer, issue_id
from src.state import Change, SiteStatus, Today, label

NONE, NA, MISSING, CONFLICT = "none", "n/a", "—", "conflict"
DISCLAIMER = ("B2B market data. 18+. Not an offer to gamble. Collected from {where} on {day}; "
              "terms may differ by location.")
EMPTY_LEGEND = ('"none" = the terms say there is no such limit · "n/a" = does not apply to this reward · '
                '"—" = not stated on the pages read · "conflict" = the pages contradict each other (link to issues). '
                'Values link to the page they were read from; hover a cell for the quote.')

NOT_APPLICABLE = {
    "free_spins": {"bonus_percent", "max_bonus"},
    "free_bets": {"bonus_percent", "free_spins", "spin_value", "wagering_multiplier", "wagering_applies_to", "max_win", "max_bet"},
}
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
CURRENCY_SYMBOL = {"GBP": "£", "USD": "$", "EUR": "€"}
TERMS_CELL_LIMIT = 49000  # Google Sheets allows 50,000 characters per cell


@dataclass
class Cell:
    value: object = None          # str | int | Decimal | bool | None
    link: str | None = None       # http(s) URL, or "#issue:<id>" / "#terms:<offer_id>" (resolved by the writer)
    note: str | None = None       # shown on hover
    fmt: str | None = None        # number format pattern
    style: str | None = None      # empty-state / issue styling, see sheets.STYLES
    checkbox: bool = False
    wrap: bool = False            # wrap long text (issue details and quotes); other cells stay one line


@dataclass
class Column:
    header: str
    width: int = 110


@dataclass
class Table:
    title: str
    columns: list[Column]
    rows: list[list[Cell]]
    legend: str = ""
    anchors: dict[str, int] = field(default_factory=dict)   # "#issue:<id>" -> index of its first row
    status_column: str | None = None                        # header whose values get coloured


# --- number formats -------------------------------------------------------------------

def _decimals(value: Decimal) -> int:
    return max(0, -Decimal(value).normalize().as_tuple().exponent)


def _pattern(value: Decimal, suffix: str = "", min_decimals: int = 0) -> str:
    d = max(_decimals(value), min_decimals)
    return ("0." + "0" * d if d else "0") + suffix


def money_format(value: Decimal, currency: str | None) -> str:
    if currency in CURRENCY_SYMBOL:
        return f'"{CURRENCY_SYMBOL[currency]}"#,##0.00'
    if currency:
        return _pattern(value, f' "{currency}"')
    return _pattern(value)


# --- one offer field -----------------------------------------------------------------

KIND = {
    "qualifying_amount": "money", "min_deposit": "money", "max_bonus": "money", "spin_value": "money",
    "total_reward_value": "money", "max_bet": "money", "max_win": "money",
    "free_spins": "count", "bonus_percent": "pct", "wagering_multiplier": "mult",
    "claim_days": "days", "use_days": "days", "expiry_days": "days", "offer_end_date": "date",
}


def _not_applicable(offer: Offer, name: str) -> bool:
    if name in NOT_APPLICABLE.get(offer.reward_type or "", set()):
        return True
    if name == "wagering_applies_to" and offer.wagering_multiplier == 0:
        return True
    # a match-bonus step that comes without spins: there are no spins to describe
    if name in ("free_spins", "spin_value") and offer.reward_type == "bonus_cash" and offer.free_spins is None:
        return True
    return False


def _quote_note(quote) -> str:
    text = quote.text if len(quote.text) <= 600 else quote.text[:600] + "…"
    return f"“{text}”\n— {quote.source_url}"


class OfferCells:
    """Cells of one offer, aware of the issues raised against it."""

    def __init__(self, offer: Offer, issues: list[Issue]) -> None:
        self.o = offer
        self.issues = [i for i in issues if i.offer_id == offer.offer_id]

    def field(self, name: str) -> Cell:
        o, value = self.o, getattr(self.o, name)
        mine = [i for i in self.issues if i.field == name]
        if value is None:
            conflict = next((i for i in mine if i.type == "conflict"), None)
            if conflict:
                return Cell(CONFLICT, link=f"#issue:{issue_id(conflict)}", note=conflict.detail, style="conflict")
            if name in o.stated_none:
                q = o.stated_none[name]
                return Cell(NONE, link=q.source_url, note=_quote_note(q), style="none")
            if _not_applicable(o, name):
                return Cell(NA, style="na")
            return Cell(MISSING, note=o.field_notes.get(name), style="missing")
        ev = o.evidence.get(name)
        cell = Cell(self._shown(name, value), link=ev.source_url if ev else None,
                    note=_quote_note(ev) if ev else None, fmt=self._format(name, value))
        if mine:  # value used, but the pages also say something else: point at the issue
            cell.style = "issue"
            cell.note = "\n\n".join(filter(None, [cell.note, *(f"Issue {issue_id(i)} ({i.type}): {i.detail}"
                                                              for i in mine)]))
        return cell

    def _shown(self, name: str, value):
        if isinstance(value, date):
            return value.isoformat()
        if name == "eligible_games" or isinstance(value, str):
            return str(value)
        return value

    def _format(self, name: str, value) -> str | None:
        kind = KIND.get(name)
        if kind == "money":
            return money_format(value, self.o.currency)
        if kind == "count":
            return "0"
        if kind == "pct":
            return _pattern(value, '"%"')
        if kind == "mult":
            return _pattern(value, '"x"')
        if kind == "days":
            return _pattern(value)
        return None

    # -- derived metrics --------------------------------------------------------

    def reward_per_unit(self) -> Cell:
        o, v = self.o, self.o.reward_per_unit
        if v is not None:
            return Cell(v, fmt="0.00", note=f"total reward {o.total_reward_value} ÷ qualifying amount "
                                           f"{o.qualifying_amount} {o.currency or ''}".strip())
        missing = [n for n in ("total_reward_value", "qualifying_amount") if getattr(o, n) is None]
        return Cell(MISSING, style="missing", note="cannot be computed: " + ", ".join(missing) + " not known")

    def over_10x(self) -> Cell:
        v = self.o.over_10x
        if v is not None:
            return Cell(v, checkbox=True)
        return Cell(NA, style="na") if _not_applicable(self.o, "wagering_multiplier") else \
            Cell(MISSING, style="missing", note="wagering not stated")

    def _turnover_unknown(self) -> Cell:
        o = self.o
        if o.wagering_applies_to == "winnings":
            return Cell(NA, style="na", note="wagering applies to free-spin winnings, not to a deposit-linked bonus")
        if o.reward_type == "free_spins" and o.bonus_percent is None:
            return Cell(NA, style="na", note="no match bonus")
        return Cell(MISSING, style="missing", note="wagering multiplier or its base not stated")

    def turnover_x_deposit(self) -> Cell:
        o, v = self.o, self.o.turnover_x_deposit
        if v is None:
            return self._turnover_unknown()
        return Cell(v, fmt=_pattern(v, '"x"'),
                    note=f"{o.bonus_percent}% ÷ 100 × {o.wagering_multiplier}x on the {o.wagering_applies_to}: "
                         f"every 1 {o.currency or 'unit'} deposited must be wagered {v.normalize():f} times")

    def required_turnover(self) -> Cell:
        o, v = self.o, self.o.required_turnover
        if v is None:
            return self._turnover_unknown()
        return Cell(v, fmt=money_format(v, o.currency),
                    note=f"at the maximum bonus {o.max_bonus} {o.currency}: {o.max_bonus} × {o.wagering_multiplier}")

    # -- identity -----------------------------------------------------------

    def operator(self) -> Cell:
        return Cell(self.o.operator, link=self.o.url)

    def issues_cell(self) -> Cell:
        if not self.issues:
            return Cell("")
        first = sorted(self.issues, key=lambda i: SEVERITY_ORDER[i.severity])[0]
        word = "issue" if len(self.issues) == 1 else "issues"
        return Cell(f"{len(self.issues)} {word}", link=f"#issue:{issue_id(first)}",
                    note="\n".join(f"{issue_id(i)}: {i.type}" for i in self.issues), style=f"sev_{first.severity}")

    def terms_hash(self) -> Cell:
        return Cell(self.o.terms_hash[:12], link=f"#terms:{self.o.offer_id}", note="sha256 of the terms text; "
                    "click for the full text")

    def licence(self) -> Cell:
        return Cell(self.o.license)

    def licence_note(self) -> Cell:
        return Cell(self.o.license_note or "", note=self.o.license_note)


def _status(last_seen: str, stale: bool) -> Cell:
    return Cell(f"STALE (last seen {last_seen})" if stale else "OK")


# --- offers_uk ------------------------------------------------------------------------

UK_COLUMNS = [
    Column("Rank", 72), Column("Product", 90), Column("Operator", 130), Column("Offer", 200),
    Column("Reward per £1 qualifying", 110), Column("Wagering", 98), Column("Wagering on", 90),
    Column("Over 10x", 65), Column("Qualifying action", 110), Column("Qualifying amount", 100),
    Column("Min deposit", 80), Column("Promo code", 105), Column("Reward type", 90), Column("Free spins", 70),
    Column("Spin value", 75), Column("Bonus %", 75), Column("Max bonus", 80), Column("Total reward value", 90),
    Column("Eligible games", 170), Column("Max bet", 70), Column("Max win", 75), Column("Days to claim", 70),
    Column("Days to use", 70), Column("Days to wager", 70), Column("Offer ends", 90), Column("Issues", 80),
    Column("Licence", 190), Column("Terms hash", 100), Column("Status", 90),
]
UK_LEGEND = ("Rank is within one product (casino / sports) by reward per £1 the player must deposit or wager "
             "to qualify; on equal value, offers without wagering come first. " + EMPTY_LEGEND)


def _wagering_class(offer: Offer) -> tuple[int, Decimal]:
    m = offer.wagering_multiplier
    if m == 0:
        return (0, Decimal(0))
    return (1, m) if m is not None else (2, Decimal(0))


def _ranks(keys: list) -> list[int | None]:
    """Competition ranking ("1, 2, 2, 4") of already sorted keys; None keys get no rank."""
    ranks, prev, rank = [], object(), 0
    for i, key in enumerate(keys):
        if key is None:
            ranks.append(None)
            continue
        if key != prev:
            rank, prev = i + 1, key
        ranks.append(rank)
    return ranks


def uk_table(today: Today, issues: list[Issue]) -> Table:
    rows = [(o, seen, stale) for o, seen, stale in today.rows if o.market == "UK"]
    products = sorted({o.product for o, _, _ in rows}, key=lambda p: (p != "casino", p))
    out = []
    for product in products:
        group = [r for r in rows if r[0].product == product]
        group.sort(key=lambda r: (r[0].reward_per_unit is None, -(r[0].reward_per_unit or 0),
                                  _wagering_class(r[0]), r[0].operator))
        keys = [None if o.reward_per_unit is None else (o.reward_per_unit, _wagering_class(o)[0])
                for o, _, _ in group]
        for (o, seen, stale), rank in zip(group, _ranks(keys)):
            c = OfferCells(o, issues)
            out.append([
                Cell(rank if rank else MISSING, fmt="0", style=None if rank else "missing"),
                Cell(o.product), c.operator(), Cell(label(o), link=o.url),
                c.reward_per_unit(), c.field("wagering_multiplier"), c.field("wagering_applies_to"), c.over_10x(),
                c.field("qualifying_action"), c.field("qualifying_amount"), c.field("min_deposit"),
                c.field("promo_code"), c.field("reward_type"), c.field("free_spins"), c.field("spin_value"),
                c.field("bonus_percent"), c.field("max_bonus"), c.field("total_reward_value"),
                c.field("eligible_games"), c.field("max_bet"), c.field("max_win"), c.field("claim_days"),
                c.field("use_days"), c.field("expiry_days"), c.field("offer_end_date"), c.issues_cell(),
                c.licence(), c.terms_hash(), _status(seen, stale),
            ])
    return Table("offers_uk", UK_COLUMNS, out, UK_LEGEND, status_column="Status")


# --- offers_crypto --------------------------------------------------------------------

CRYPTO_COLUMNS = [
    Column("Rank", 72), Column("Operator", 120), Column("Step", 110), Column("Turnover × deposit", 95),
    Column("Wagering", 98), Column("Wagering on", 100), Column("Days to wager", 80), Column("Max bet", 110),
    Column("Currency", 92), Column("Bonus %", 75), Column("Max bonus", 90),
    Column("Required turnover at max bonus", 115), Column("Min deposit", 110), Column("Promo code", 85),
    Column("Free spins", 70), Column("Days to use", 70), Column("Max win", 110), Column("Days to claim", 82),
    Column("Eligible games", 150), Column("Issues", 80), Column("Licence", 230), Column("Licence note", 260),
    Column("Terms hash", 100), Column("Status", 90),
]
CRYPTO_LEGEND = ("Sorted by turnover × deposit = bonus % ÷ 100 × wagering: how many times every unit deposited "
                 "must be wagered (lower is easier; does not depend on the deposit size). Required turnover at the "
                 "maximum bonus is for reference. " + EMPTY_LEGEND)


def crypto_table(today: Today, issues: list[Issue]) -> Table:
    rows = [(o, seen, stale) for o, seen, stale in today.rows if o.market == "crypto"]
    rows.sort(key=lambda r: (r[0].product != "casino", r[0].turnover_x_deposit is None,
                             r[0].turnover_x_deposit or 0, r[0].operator, r[0].offer_label or ""))
    keys = [None if o.turnover_x_deposit is None else (o.product, o.turnover_x_deposit) for o, _, _ in rows]
    out = []
    for (o, seen, stale), rank in zip(rows, _ranks(keys)):
        c = OfferCells(o, issues)
        out.append([
            Cell(rank if rank else MISSING, fmt="0", style=None if rank else "missing"),
            c.operator(), Cell(label(o), link=o.url), c.turnover_x_deposit(), c.field("wagering_multiplier"),
            c.field("wagering_applies_to"), c.field("expiry_days"), c.field("max_bet"), c.field("currency"),
            c.field("bonus_percent"), c.field("max_bonus"), c.required_turnover(), c.field("min_deposit"),
            c.field("promo_code"), c.field("free_spins"), c.field("use_days"), c.field("max_win"),
            c.field("claim_days"), c.field("eligible_games"), c.issues_cell(), c.licence(), c.licence_note(),
            c.terms_hash(), _status(seen, stale),
        ])
    return Table("offers_crypto", CRYPTO_COLUMNS, out, CRYPTO_LEGEND, status_column="Status")


# --- issues ---------------------------------------------------------------------------

ISSUE_COLUMNS = [
    Column("Issue", 200), Column("Severity", 90), Column("Type", 90), Column("Operator", 110),
    Column("Field", 120), Column("Detail", 260), Column("Quote", 420), Column("Source page", 260),
    Column("First seen", 85), Column("Last seen", 85),
]
ISSUES_LEGEND = ("Places where an operator's own pages contradict each other (conflict = high), can be read two "
                 "ways (ambiguous = medium) or contain a typo (low). One row per quote; facts and quotes only.")


def issues_table(today: Today) -> Table:
    items = sorted(today.issues, key=lambda t: (SEVERITY_ORDER[t[0].severity], t[0].operator, t[0].field))
    rows, anchors = [], {}
    for issue, first, last in items:
        iid = issue_id(issue)
        anchors[f"#issue:{iid}"] = len(rows)
        for n, q in enumerate(issue.quotes):
            rows.append([
                Cell(f"{issue.operator_id}/{issue.field}", note=iid), Cell(issue.severity, style=f"sev_{issue.severity}"), Cell(issue.type),
                Cell(issue.operator), Cell(issue.field), Cell(issue.detail if n == 0 else "", wrap=True),
                Cell(q.text, link=q.source_url, wrap=True), Cell(q.source_url, link=q.source_url),
                Cell(first), Cell(last),
            ])
    return Table("issues", ISSUE_COLUMNS, rows, ISSUES_LEGEND, anchors)


# --- changes --------------------------------------------------------------------------

CHANGE_COLUMNS = [
    Column("Date", 85), Column("Operator", 120), Column("Offer", 220), Column("Field", 130),
    Column("Was", 160), Column("Now", 160), Column("Source page", 300),
]
CHANGES_LEGEND = ("Change log, newest first: a field of an offer changed, its terms text changed (hash shown), "
                  "or an offer appeared/disappeared. Operators that could not be read on a day (STALE) log nothing "
                  "for that day. The first observation of an operator is the baseline, not a change.")


def changes_table(changes: list[dict]) -> Table:
    rows = [[Cell(c["date"]), Cell(c["operator"]), Cell(c["offer"]), Cell(c["field"]), Cell(c["was"]),
             Cell(c["now"]), Cell(c["source_url"], link=c["source_url"] or None)] for c in changes]
    return Table("changes", CHANGE_COLUMNS, rows, CHANGES_LEGEND)


# --- terms ----------------------------------------------------------------------------

TERMS_COLUMNS = [
    Column("Operator", 110), Column("Offer", 220), Column("Terms hash", 100), Column("Last changed", 90),
    Column("First seen", 85), Column("Characters", 80), Column("Source pages", 300), Column("Terms text", 700),
]
TERMS_LEGEND = ("Full terms text as read (whitespace normalised), its sha256 hash and the date the text last "
                "changed. One row per offer; choices and deposit steps of one offer share it.")


def terms_table(today: Today, terms_state: dict[str, dict]) -> Table:
    by_offer: dict[str, list[Offer]] = {}
    for o, _, _ in today.rows:
        by_offer.setdefault(o.offer_id, []).append(o)
    rows, anchors = [], {}
    for offer_id, offers in sorted(by_offer.items(), key=lambda kv: (kv[1][0].market != "UK", kv[1][0].operator)):
        o = offers[0]
        text = "\n\n---\n\n".join(dict.fromkeys(x.terms_text for x in offers))
        urls = list(dict.fromkeys([x.url for x in offers] + [q.source_url for x in offers
                                                              for q in x.evidence.values()]))
        st = terms_state.get(offer_id, {})
        note = None
        if len(text) > TERMS_CELL_LIMIT:
            note = f"Text truncated to {TERMS_CELL_LIMIT} of {len(text)} characters (sheet cell limit)."
            text = text[:TERMS_CELL_LIMIT]
        anchors[f"#terms:{offer_id}"] = len(rows)
        offer_names = "; ".join(dict.fromkeys(label(x).split(":")[0] for x in offers))
        rows.append([Cell(o.operator, link=o.url), Cell(offer_names if len(offers) > 1 else label(o)),
                     Cell(o.terms_hash[:12], note=o.terms_hash), Cell(st.get("last_changed", today.day)),
                     Cell(st.get("first_seen", today.day)), Cell(len(" ".join(text.split())), fmt="#,##0"),
                     Cell("\n".join(urls), link=urls[0]), Cell(text, note=note)])
    return Table("terms", TERMS_COLUMNS, rows, TERMS_LEGEND, anchors)


# --- status ---------------------------------------------------------------------------

STATUS_COLUMNS = [
    Column("Operator", 130), Column("Market", 70), Column("Status", 85), Column("Last run", 85),
    Column("Last success", 90), Column("Offer rows", 75), Column("Pages read", 75), Column("Reason / note", 520),
]
STATUS_LEGEND = ("Run health per operator. STALE = a page was blocked, geo-blocked, failed or changed layout; "
                 "nothing is worked around, the last known offers are shown and flagged. DISABLED = not monitored "
                 "(reason given).")


def status_table(statuses: dict[str, SiteStatus], disabled: list[dict]) -> Table:
    rows = []
    for s in sorted(statuses.values(), key=lambda s: (s.market != "UK", s.operator)):
        rows.append([Cell(s.operator), Cell(s.market), Cell("STALE" if s.stale else "OK"), Cell(s.last_run or ""),
                     Cell(s.last_success or ""), Cell(s.offers_today, fmt="0"),
                     Cell(f"{s.pages_ok}/{s.pages_total}"), Cell(s.reason)])
    for op in sorted(disabled, key=lambda o: (o["market"] != "UK", o["operator"])):
        rows.append([Cell(op["operator"]), Cell(op["market"]), Cell("DISABLED"), Cell(""), Cell(""), Cell(""),
                     Cell(""), Cell(" ".join(str(op.get("disabled_reason") or "").split()))])
    return Table("status", STATUS_COLUMNS, rows, STATUS_LEGEND, status_column="Status")


# --- history --------------------------------------------------------------------------

HISTORY_FIELDS = (
    "product", "qualifying_action", "qualifying_amount", "min_deposit", "promo_code", "reward_type", "currency",
    "bonus_percent", "max_bonus", "free_spins", "spin_value", "total_reward_value", "eligible_games",
    "wagering_multiplier", "wagering_applies_to", "max_bet", "max_win", "claim_days", "use_days", "expiry_days",
    "offer_end_date",
)
HISTORY_HEADERS = ["date", "operator_id", "operator", "market", "row_key", "offer"] + list(HISTORY_FIELDS) + [
    "reward_per_unit", "turnover_x_deposit", "required_turnover", "over_10x", "terms_hash"]
HISTORY_LEGEND = ("One snapshot per day of every offer read that day (stale operators are not re-recorded). "
                  "Raw values: empty = not stated, \"none\" = stated as none.")


def history_rows(today: Today) -> list[list]:
    from src.models import row_key
    from src.state import display_value
    out = []
    for o, _, stale in today.rows:
        if stale:
            continue
        values = []
        for name in HISTORY_FIELDS:
            v = display_value(o, name)
            values.append("" if v == MISSING else v)
        derived = [o.reward_per_unit, o.turnover_x_deposit, o.required_turnover]
        out.append([today.day, o.operator_id, o.operator, o.market, row_key(o), label(o), *values,
                    *["" if d is None else f"{d.normalize():f}" for d in derived],
                    "" if o.over_10x is None else str(o.over_10x).upper(), o.terms_hash])
    return out
