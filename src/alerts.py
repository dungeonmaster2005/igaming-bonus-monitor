"""Telegram alerts: grouped, neutral, only when something changed.

Environment: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID. Groups, thresholds and limits come
from config/alerts.yaml. No message on the first run (baseline), none for stale
operators (they produce no changes), none when nothing changed.
"""

from __future__ import annotations

import html
import logging
import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

import requests

from src.models import Issue, Offer
from src.state import Change, Today, label

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4096
GROUP_TITLES = {
    "numeric_change": "Changed values",
    "terms_change": "Terms text changed",
    "offer_appeared": "New offers",
    "offer_disappeared": "Offers no longer listed",
    "issue_opened": "New issues in the terms",
    "issue_closed": "Issues resolved",
    "over_10x": "Wagering above {cap}x (UK)",
    "offer_ending": "Offers ending within {days} days",
}
FIELD_LABELS = {
    "qualifying_amount": "qualifying amount", "min_deposit": "min deposit", "bonus_percent": "bonus %",
    "max_bonus": "max bonus", "free_spins": "free spins", "spin_value": "spin value",
    "total_reward_value": "total reward value", "wagering_multiplier": "wagering", "max_bet": "max bet",
    "max_win": "max win", "claim_days": "days to claim", "use_days": "days to use", "expiry_days": "days to wager",
    "offer_end_date": "offer ends",
}
MONEY = {"qualifying_amount", "min_deposit", "max_bonus", "spin_value", "total_reward_value", "max_bet", "max_win"}
SYMBOL = {"GBP": "£", "USD": "$", "EUR": "€"}


@dataclass
class Line:
    group: str
    text: str          # already HTML-escaped


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


def _who(c_or_offer) -> str:
    if isinstance(c_or_offer, Change):
        offer = c_or_offer.offer
        return f"{c_or_offer.operator} · {offer}" if offer != "Welcome offer" else c_or_offer.operator
    lab = label(c_or_offer)
    return f"{c_or_offer.operator} · {lab}" if lab != "Welcome offer" else c_or_offer.operator


def build_lines(today: Today, config: dict, ending: list[tuple[Offer, int]]) -> list[Line]:
    numeric = set(config["numeric_fields"])
    lines: list[Line] = []
    for c in today.changes:
        if c.field in numeric:
            text = (f"{_e(_who(c))} — {FIELD_LABELS.get(c.field, c.field)}: "
                    f"{_e(_value(c.field, c.was, c.currency))} → <b>{_e(_value(c.field, c.now, c.currency))}</b> "
                    f"({_a(c.source_url, 'source')})")
            lines.append(Line("numeric_change", text))
        elif c.field == "terms_text":
            frags = "".join(f"\n    <i>{_e(f)}</i>" for f in c.fragments)
            lines.append(Line("terms_change", f"{_a(c.source_url, _who(c))} — hash {c.was} → {c.now}{frags}"))
        elif c.field == "offer" and c.now == "appeared":
            lines.append(Line("offer_appeared", _a(c.source_url, _who(c))))
        elif c.field == "offer" and c.now == "disappeared":
            lines.append(Line("offer_disappeared", f"{_a(c.source_url, _who(c))} — not found on the page today"))
    for issue in today.events.issues_opened:
        lines.append(Line("issue_opened", _issue_line(issue)))
    for issue in today.events.issues_closed:
        lines.append(Line("issue_closed", _issue_line(issue, closed=True)))
    for offer in today.events.over_10x:
        ev = offer.evidence.get("wagering_multiplier")
        lines.append(Line("over_10x", f"{_e(_who(offer))} — wagering {offer.wagering_multiplier.normalize():f}x "
                                      f"on {_e(offer.wagering_applies_to or 'base not stated')} "
                                      f"({_a(ev.source_url if ev else offer.url, 'source')})"))
    for offer, left in ending:
        ev = offer.evidence.get("offer_end_date")
        when = "today" if left == 0 else f"in {left} day" + ("" if left == 1 else "s")
        lines.append(Line("offer_ending", f"{_e(_who(offer))} — ends {offer.offer_end_date.isoformat()} ({when}) "
                                          f"({_a(ev.source_url if ev else offer.url, 'source')})"))
    return lines


def _issue_line(issue: Issue, closed: bool = False) -> str:
    url = issue.quotes[0].source_url if issue.quotes else None
    detail = "" if closed else f": {_e(issue.detail)}"
    return f"{_e(issue.operator)} — {_e(issue.field)}, {issue.type} ({issue.severity}){detail} ({_a(url, 'source')})"


def build_message(today: Today, config: dict, ending: list[tuple[Offer, int]],
                  sheet_url: str | None = None, heading_note: str | None = None) -> str | None:
    """The whole message, or None when there is nothing to report (and on the first run)."""
    if today.events.first_run:
        return None
    lines = build_lines(today, config, ending)
    if not lines:
        return None
    limit = int(config["max_lines_per_group"])
    day = date.fromisoformat(today.day)
    parts = [f"<b>Casino welcome-offer monitor — {day:%d %b %Y}</b>"]
    if heading_note:
        parts.append(f"<i>{_e(heading_note)}</i>")
    for group in config["groups"]:
        items = [ln for ln in lines if ln.group == group]
        if not items:
            continue
        title = GROUP_TITLES[group].format(cap=config.get("over_10x_multiplier", 10),
                                           days=config.get("offer_ending_days", 7))
        block = [f"\n<b>{_e(title)} ({len(items)})</b>"] + [f"• {ln.text}" for ln in items[:limit]]
        if len(items) > limit:
            block.append(f"…and {len(items) - limit} more")
        parts.append("\n".join(block))
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
