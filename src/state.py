"""What the monitor remembers between runs (committed by CI, no secrets inside).

data/site_status.json   per-operator run health (adapted from eu-price-monitor)
data/offers_latest.json last known offers, issues and terms hashes of every operator
data/changes.json       the change log shown on the "changes" sheet

An operator is STALE when a page could not be fetched (block, geo-block, network) or
its parser reported a layout change. A stale operator produces no changes and no
alerts; its last known offers are carried over and flagged, so a blocked site never
looks like "the offer disappeared".
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from src.models import (
    PARSED_FIELDS, Issue, Offer, issue_from_dict, issue_id, issue_to_dict, offer_from_dict, offer_to_dict, row_key,
)

log = logging.getLogger(__name__)

DATA_DIR = Path("data")
FORGET_AFTER_DAYS = 30
# Fields compared between runs: everything parsed from the terms, plus the product.
TRACKED_FIELDS = ("product",) + PARSED_FIELDS


@dataclass
class OperatorRun:
    """Outcome of one operator today."""

    operator_id: str
    operator: str
    market: str
    pages_ok: int
    pages_total: int
    offers: list[Offer] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def stale(self) -> bool:
        return bool(self.problems)


@dataclass
class SiteStatus:
    operator_id: str
    operator: str
    market: str
    last_run: str | None = None
    last_success: str | None = None
    offers_today: int = 0
    pages_ok: int = 0
    pages_total: int = 0
    stale: bool = False
    reason: str = ""


@dataclass
class Change:
    date: str
    operator_id: str
    operator: str
    row: str           # row_key
    offer: str         # human label
    field: str         # a tracked field, "offer" (appeared/disappeared) or "terms_text"
    was: str
    now: str
    source_url: str = ""


class State:
    def __init__(self, data_dir: Path = DATA_DIR) -> None:
        self.dir = data_dir
        self.status: dict[str, SiteStatus] = {
            k: SiteStatus(**v) for k, v in self._load("site_status.json", {}).items()}
        latest = self._load("offers_latest.json", {})
        self.offers: dict[str, dict] = latest.get("offers", {})      # row_key -> offer dict + last_seen
        self.issues: dict[str, dict] = latest.get("issues", {})      # issue_id -> issue dict + first/last seen
        self.terms: dict[str, dict] = latest.get("terms", {})        # offer_id -> hash, first_seen, last_changed
        self.changes: list[dict] = self._load("changes.json", [])

    def _load(self, name: str, default):
        path = self.dir / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

    # -- one run -----------------------------------------------------------

    def apply(self, runs: list[OperatorRun], today: date) -> "Today":
        """Record today's runs; return today's dataset (fresh + carried-over) and new changes."""
        day = today.isoformat()
        rows: list[tuple[Offer, str, bool]] = []   # offer, last_seen, stale
        issues: list[tuple[Issue, str, str]] = []  # issue, first_seen, last_seen
        new_changes: list[Change] = []
        for run in runs:
            self._record_status(run, day)
            if run.stale:
                log.warning("%s: STALE — %s", run.operator_id, "; ".join(run.problems))
                for key, data in self.offers.items():
                    if data["operator_id"] == run.operator_id:
                        rows.append((offer_from_dict(data["offer"]), data["last_seen"], True))
                for iid, data in self.issues.items():
                    if data["issue"]["operator_id"] == run.operator_id:
                        issues.append((issue_from_dict(data["issue"]), data["first_seen"], data["last_seen"]))
                continue
            new_changes += self._diff(run, day)
            for offer in run.offers:
                self.offers[row_key(offer)] = {"operator_id": run.operator_id, "last_seen": day,
                                               "offer": offer_to_dict(offer)}
                rows.append((offer, day, False))
            self._update_terms(run.offers, day)
            seen_ids = set()
            for issue in run.issues:
                iid = issue_id(issue)
                seen_ids.add(iid)
                first = self.issues.get(iid, {}).get("first_seen", day)
                self.issues[iid] = {"first_seen": first, "last_seen": day, "issue": issue_to_dict(issue)}
                issues.append((issue, first, day))
            for iid in [i for i, d in self.issues.items()
                        if d["issue"]["operator_id"] == run.operator_id and i not in seen_ids]:
                del self.issues[iid]  # resolved: the terms no longer contradict themselves
        self.changes = [asdict(c) for c in new_changes] + self.changes
        self._forget_old(today)
        return Today(day, rows, issues, new_changes)

    def _record_status(self, run: OperatorRun, day: str) -> None:
        s = self.status.get(run.operator_id) or SiteStatus(run.operator_id, run.operator, run.market)
        s.operator, s.market, s.last_run = run.operator, run.market, day
        s.pages_ok, s.pages_total = run.pages_ok, run.pages_total
        s.stale = run.stale
        s.reason = "; ".join(run.problems)
        if not run.stale:
            s.last_success = day
            s.offers_today = len(run.offers)
        self.status[run.operator_id] = s

    def _diff(self, run: OperatorRun, day: str) -> list[Change]:
        """Changes against the last known offers of this operator (none on its first run)."""
        before = {k: offer_from_dict(d["offer"]) for k, d in self.offers.items()
                  if d["operator_id"] == run.operator_id}
        if not before:
            return []  # first observation is the baseline, not a change
        after = {row_key(o): o for o in run.offers}
        changes = []

        def change(key, offer, field_name, was, now):
            changes.append(Change(day, run.operator_id, run.operator, key, label(offer), field_name, was, now,
                                  offer.url))

        for key in after.keys() - before.keys():
            change(key, after[key], "offer", "—", "appeared")
        for key in before.keys() - after.keys():
            change(key, before[key], "offer", "listed", "disappeared")
            del self.offers[key]
        for key in sorted(after.keys() & before.keys()):
            old, new = before[key], after[key]
            for name in TRACKED_FIELDS:
                was, now = display_value(old, name), display_value(new, name)
                if was != now:
                    ev = new.evidence.get(name)
                    changes.append(Change(day, run.operator_id, run.operator, key, label(new), name, was, now,
                                          ev.source_url if ev else new.url))
            if old.terms_hash != new.terms_hash:
                change(key, new, "terms_text", old.terms_hash[:12], new.terms_hash[:12])
        return changes

    def _update_terms(self, offers: list[Offer], day: str) -> None:
        for offer in offers:
            t = self.terms.get(offer.offer_id)
            if t is None:
                self.terms[offer.offer_id] = {"hash": offer.terms_hash, "first_seen": day, "last_changed": day}
            elif t["hash"] != offer.terms_hash:
                t.update(hash=offer.terms_hash, last_changed=day)

    def _forget_old(self, today: date) -> None:
        for key in [k for k, d in self.offers.items()
                    if (today - date.fromisoformat(d["last_seen"])).days > FORGET_AFTER_DAYS]:
            del self.offers[key]

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._write("site_status.json", {k: asdict(v) for k, v in sorted(self.status.items())})
        self._write("offers_latest.json", {"offers": dict(sorted(self.offers.items())),
                                           "issues": dict(sorted(self.issues.items())),
                                           "terms": dict(sorted(self.terms.items()))})
        self._write("changes.json", self.changes)

    def _write(self, name: str, payload) -> None:
        (self.dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


@dataclass
class Today:
    day: str
    rows: list[tuple[Offer, str, bool]]
    issues: list[tuple[Issue, str, str]]
    changes: list[Change]


def label(offer: Offer) -> str:
    return offer.offer_label or "Welcome offer"


def display_value(offer: Offer, name: str) -> str:
    """Text form of a field for the change log: value, "none" (stated) or "—" (not stated)."""
    value = getattr(offer, name)
    if value is None:
        return "none" if name in offer.stated_none else "—"
    if isinstance(value, date):
        return value.isoformat()
    return f"{value.normalize():f}" if hasattr(value, "normalize") else str(value)
