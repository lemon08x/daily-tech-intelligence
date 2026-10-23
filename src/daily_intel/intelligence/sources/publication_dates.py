"""Read explicit publication dates, never inferred page modification dates."""
from __future__ import annotations

import json
from datetime import datetime
from html.parser import HTMLParser

from daily_intel.intelligence.sources.common import parse_iso_datetime


class _Dates(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: list[str] = []
        self.jsonld: list[str] = []
        self._script: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and (
            attrs.get("property", "").lower() == "article:published_time"
            or attrs.get("itemprop", "").lower() == "datepublished"
        ):
            self.values.append(attrs.get("content", ""))
        if tag == "time" and attrs.get("itemprop", "").lower() == "datepublished":
            self.values.append(attrs.get("datetime", ""))
        if tag == "script" and attrs.get("type", "").lower() == "application/ld+json":
            self._script = []

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._script is not None:
            self.jsonld.append("".join(self._script))
            self._script = None


def explicit_publication_date(html: str) -> datetime | None:
    parser = _Dates()
    parser.feed(html)

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            kinds = value.get("@type", [])
            kinds = [kinds] if isinstance(kinds, str) else kinds
            if any(k in {"Article", "NewsArticle", "BlogPosting", "ScholarlyArticle", "TechArticle"} for k in kinds):
                if isinstance(value.get("datePublished"), str):
                    parser.values.append(value["datePublished"])
            # Only graph nodes, not nested related articles or citations.
            visit(value.get("@graph", []))

    for block in parser.jsonld:
        try:
            visit(json.loads(block))
        except (ValueError, TypeError):
            continue
    dates = [parse_iso_datetime(value) for value in parser.values]
    return min((value for value in dates if value is not None), default=None)
