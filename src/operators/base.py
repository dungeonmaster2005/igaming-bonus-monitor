"""Helpers shared by the operator parsers."""

from __future__ import annotations

import re
from datetime import date

from bs4 import BeautifulSoup

from src.models import Offer
from src.parse_common import segments


class LayoutChanged(Exception):
    """A page no longer has the blocks a parser relies on."""


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def new_offer(op: dict, today: date, offer_id: str, url: str, terms_text: str, **fields) -> Offer:
    return Offer(date=today, operator_id=op["id"], operator=op["operator"], market=op["market"],
                 license=op["license"], license_note=op.get("license_note"), url=url,
                 offer_id=offer_id, terms_text=terms_text, **fields)


def block_segments(html: str, element_id: str | None = None, selector: str | None = None) -> list[str]:
    """Sentences of one element (by id or CSS selector); LayoutChanged if it is missing."""
    page = soup(html)
    element = page.find(id=element_id) if element_id else page.select_one(selector)
    if element is None:
        raise LayoutChanged(f"{element_id or selector} not found")
    return segments(element)


def between(items: list[str], start: str, stop: str | None = None, include_start: bool = True) -> list[str]:
    """Items from the first one matching regex `start` up to (not including) one matching `stop`."""
    out, inside = [], False
    for item in items:
        if not inside and re.search(start, item):
            inside = True
            if include_start:
                out.append(item)
            continue
        if inside:
            if stop and re.search(stop, item):
                break
            out.append(item)
    return out
