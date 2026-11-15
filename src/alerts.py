"""Telegram alerts: one block per operator, neutral wording, only when something changed.

Environment: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID. What is reported, thresholds and limits
come from config/alerts.yaml. No message on the first run (baseline), none for stale
operators (they produce no changes), none when nothing changed.

Block of an operator: changed fields (was → now), flags (over 10x, offer ending, issue
opened/resolved) and the terms-text passages that no changed field explains. When every
changed passage is explained by a field ("x10" → "x20" with wagering 10 → 20) the block
says "terms text updated accordingly" instead. Noisy sources get no text lines.
Offers that appeared / are no longer listed form one block at the end. Hashes stay on the
"terms" sheet; the message never shows them.
"""

from __future__ import annotations

import html
import logging
import os
from collections import OrderedDict
from datetime import date
from decimal import Decimal, InvalidOperation

import requests

from src.models import Issue, Offer
from src.state import Change, Today, label
from src.terms_text import Passage, render_passages

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4096
FIELD_LABELS = {
    "qualifying_action": "qualifying action", "qualifying_amount": "qualifying amount",
    "min_deposit": "min deposit", "promo_code": "promo code", "reward_type": "reward type",
    "bonus_percent": "bonus %", "max_bonus": "max bonus", "free_spins": "free spins", "spin_value": "spin value",
    "total_reward_value": "total reward value", "eligible_games": "eligible games",
    "wagering_multiplier": "wagering", "wagering_applies_to": "wagering on", "max_bet": "max bet",
    "max_win": "max win", "claim_days": "days to claim", "use_days": "days to use", "expiry_days": "days to wager",
    "offer_end_date": "offer ends", "product": "product",
}
MONEY = {"qualifying_amount", "min_deposit", "max_bonus", "spin_value", "total_reward_value", "max_bet", "max_win"}
SYMBOL = {"GBP": "£", "USD": "$", "EUR": "€"}


def _e(text) -> str:
    return html.escape(str(text), quote=False)


def _a(url: str | None, text: str) -> str:
    if not url:
        return _e(text)
    return f'<a href="{html.escape(url, quote=True)}">{_e(text)}</a>'


def _value(field: str, raw: str, currency: str | None) -> str:
    """Change-log value with its unit: £10.00, 0.0005 BTC, 10x, 100%, 7 days; none / — as they are."""
    if raw in ("none", "—", ""):
        return raw or "—"
    try:
        number = Decimal(raw)
    except InvalidOperation:
        return raw  # a date or text
    if field in MONEY:
        if currency in SYMBOL:
            return f"{SYMBOL[currency]}{number:,.2f}"
        return f"{number.normalize():f} {currency or ''}".strip()
    if field == "wagering_multiplier":
        return f"{number.normalize():f}x"
    if field == "bonus_percent":
        return f"{number.normalize():f}%"
    if field.endswith("_days"):
        return f"{number.normalize():f} day" + ("" if number == 1 else "s")
    return f"{number.normalize():f}"


def _rows_label(labels: list[str], all_rows: int) -> str:
    """'' for a single-row offer, 'all 4 rows' when every row changed the same way, else short labels."""
    labels = [lb for lb in labels if lb != "Welcome offer"]
    if not labels:
        return ""
    if len(labels) > 1 and len(labels) == all_rows:
        return f"all {all_rows} {'steps' if labels[0].startswith('Deposit') else 'choices'}: "
    return ", ".join(lb.split(":")[0] for lb in labels) + ": "


class Block:
    def __init__(self, title: str) -> None:
        self.title = title
        self.lines: list[str] = []


def _field_lines(changes: list[Change], rows_per_offer: dict[str, int]) -> list[str]:
    """Same change on several rows of one offer (e.g. all deposit steps) is one line."""
    grouped: "OrderedDict[tuple, list[Change]]" = OrderedDict()
    for c in changes:
        grouped.setdefault((c.row.split("#")[0], c.field, c.was, c.now), []).append(c)
    out = []
    for (offer_id, fld, was, now), items in grouped.items():
        c = items[0]
        prefix = _rows_label([x.offer for x in items], rows_per_offer.get(offer_id, 1))
        out.append(f"{_e(prefix)}{FIELD_LABELS.get(fld, fld)}: {_e(_value(fld, was, c.currency))} → "
                   f"<b>{_e(_value(fld, now, c.currency))}</b> ({_a(c.source_url, 'source')})")
    return out


def _issue_line(issue: Issue, closed: bool) -> str:
    url = issue.quotes[0].source_url if issue.quotes else None
    if closed:
        return f"issue resolved: {_e(issue.field)}, {issue.type} ({_a(url, 'source')})"
    return (f"new issue: {_e(issue.field)}, {issue.type} ({issue.severity}): {_e(issue.detail)} "
            f"({_a(url, 'source')})")


def _offer_name(offer: Offer) -> str:
    lab = label(offer)
    return "" if lab == "Welcome offer" else f"{lab.split(':')[0]}: "


def build_message(today: Today, config: dict, ending: list[tuple[Offer, int]],
                  sheet_url: str | None = None, heading_note: str | None = None) -> str | None:
    """The whole message, or None when there is nothing to report (and on the first run)."""
    if today.events.first_run:
        return None
    include = set(config["include"])
    fields = set(config["fields"])
    rows_per_offer: dict[str, int] = {}
    markets: dict[str, str] = {}
    for o, _, _ in today.rows:
        rows_per_offer[o.offer_id] = rows_per_offer.get(o.offer_id, 0) + 1
        markets[o.operator] = o.market
    blocks: "OrderedDict[str, Block]" = OrderedDict()

    def block(operator: str) -> Block:
        if operator not in blocks:
            blocks[operator] = Block(operator)
        return blocks[operator]

    by_operator: dict[str, list[Change]] = {}
    for c in today.changes:
        by_operator.setdefault(c.operator, []).append(c)
        markets.setdefault(c.operator, c.market)
    listed: list[str] = []
    for operator, changes in by_operator.items():
        if "fields" in include:
            lines = _field_lines([c for c in changes if c.field in fields], rows_per_offer)
            if lines:
                block(operator).lines += lines
        for c in changes:
            if c.field == "offer" and "offers_listed" in include:
                what = "new offer" if c.now == "appeared" else "no longer listed"
                listed.append(f"{_e(operator)} · {_a(c.source_url, c.offer)} — {what}")
    noisy = {o.operator for o, _, _ in today.rows if o.operator_id in today.events.noisy}
    for offer in today.events.over_10x if "over_10x" in include else []:
        ev = offer.evidence.get("wagering_multiplier")
        block(offer.operator).lines.append(
            f"flag: {_e(_offer_name(offer))}wagering {offer.wagering_multiplier.normalize():f}x is above "
            f"{config.get('over_10x_multiplier', 10)}x ({_a(ev.source_url if ev else offer.url, 'source')})")
    for offer, left in ending if "offer_ending" in include else []:
        ev = offer.evidence.get("offer_end_date")
        when = "today" if left == 0 else f"in {left} day" + ("" if left == 1 else "s")
        block(offer.operator).lines.append(
            f"flag: {_e(_offer_name(offer))}offer ends {offer.offer_end_date.isoformat()} ({when}) "
            f"({_a(ev.source_url if ev else offer.url, 'source')})")
    for issue in today.events.issues_opened if "issue_opened" in include else []:
        block(issue.operator).lines.append("flag: " + _issue_line(issue, closed=False))
    for issue in today.events.issues_closed if "issue_closed" in include else []:
        block(issue.operator).lines.append(_issue_line(issue, closed=True))
    if "terms_text" in include:
        for operator, changes in by_operator.items():
            if operator in noisy:
                continue  # noisy source: no text alerts, field alerts above stay
            for c in [c for c in changes if c.field == "terms_text"]:
                passages = [Passage(**p) for p in c.fragments]
                open_ = [p for p in passages if not p.covered]
                name = "" if c.offer == "Welcome offer" or rows_per_offer.get(c.row.split("#")[0], 1) > 1 \
                    else f"{c.offer.split(':')[0]}: "
                if not open_:
                    block(operator).lines.append(f"{_e(name)}terms text updated accordingly")
                    continue
                frags = render_passages(open_, int(config["terms_diff_fragments"]), int(config["terms_fragment_chars"]))
                block(operator).lines.append(f"{_e(name)}terms text changed ({_a(c.source_url, 'source')}):"
                                             + "".join(f"\n    <i>{_e(f)}</i>" for f in frags))

    notes = []
    for operator, days in today.events.became_noisy:
        notes.append(f"{_e(operator)}: the terms text changed on {len(days)} consecutive days "
                     f"({_e(', '.join(days))}); marked noisy on the status sheet, text alerts paused, "
                     f"field alerts continue.")
    for operator in today.events.noise_cleared:
        notes.append(f"{_e(operator)}: terms text stable again; text alerts resumed.")

    if not blocks and not listed and not notes:
        return None
    day = date.fromisoformat(today.day)
    parts = [f"<b>Casino welcome-offer monitor — {day:%d %b %Y}</b>"]
    if heading_note:
        parts.append(f"<i>{_e(heading_note)}</i>")
    limit = int(config["max_lines_per_operator"])
    order = sorted(blocks.values(), key=lambda b: (markets.get(b.title) != "UK", b.title))
    for b in order:
        lines = [f"• {ln}" for ln in b.lines[:limit]]
        if len(b.lines) > limit:
            lines.append(f"…and {len(b.lines) - limit} more")
        market = markets.get(b.title, "")
        parts.append(f"\n<b>{_e(b.title)}</b>{' · ' + _e(market) if market else ''}\n" + "\n".join(lines))
    if listed:
        cap = int(config["max_lines_offers_block"])
        lines = [f"• {ln}" for ln in listed[:cap]] + ([f"…and {len(listed) - cap} more"] if len(listed) > cap else [])
        parts.append("\n<b>Offers listed / no longer listed</b>\n" + "\n".join(lines))
    if notes:
        parts.append("\n<b>Monitoring notes</b>\n" + "\n".join(f"• {n}" for n in notes))
    footer = []
    if today.events.stale:
        footer.append(f"<i>Not read today, no alerts: {_e(', '.join(sorted(today.events.stale)))}.</i>")
    if sheet_url:
        footer.append(_a(sheet_url, "Full table"))
    footer.append(f"<i>{_e(config['caption'])}</i>")
    message = "\n".join(parts) + "\n\n" + "\n".join(footer)
    if len(message) > TELEGRAM_LIMIT:  # never fail on length; keep the caption
        tail = "\n…(truncated)\n<i>" + _e(config["caption"]) + "</i>"
        message = message[: TELEGRAM_LIMIT - len(tail)].rsplit("\n", 1)[0] + tail
    return message


def telegram_configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def send_telegram(text: str) -> None:
    token, chat_id = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=20,
    )
    if not resp.ok:
        # never log the URL: it contains the bot token
        raise RuntimeError(f"Telegram API error {resp.status_code}: {resp.text[:200]}")
    log.info("Telegram message sent (%d chars)", len(text))
