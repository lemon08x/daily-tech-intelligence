from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from time import struct_time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


USER_AGENT = "DailyIntel/0.3 (+local research digest)"


def as_datetime(value: struct_time | None, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    return datetime(*value[:6], tzinfo=timezone.utc)


def parse_iso_datetime(value: str | None, fallback: datetime | None = None) -> datetime | None:
    if not value:
        return fallback
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def plain_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", html.unescape(value or ""))
    return re.sub(r"\s+", " ", value).strip()


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")]
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), "")
    )


def document_id(source_id: str, external_id: str) -> str:
    return hashlib.sha256(f"{source_id}\0{external_id}".encode()).hexdigest()[:24]


def content_hash(title: str, content: str) -> str:
    normalized = re.sub(r"\W+", "", f"{title}{content}").lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def document_source_id(config: dict) -> str:
    """Return the evidence publisher, which may differ from the discovery adapter."""
    return str(config.get("publisher_id") or config["id"])


def effective_limit(config: dict, requested: int) -> int:
    configured = int(config.get("max_items", requested))
    return max(1, min(requested, configured))


def _contains_keyword(text: str, keyword: str) -> bool:
    keyword = keyword.casefold().strip()
    if not keyword:
        return False
    if len(keyword) <= 3 and keyword.isalnum():
        return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text) is not None
    return keyword in text


def passes_keyword_filters(title: str, summary: str, config: dict) -> bool:
    haystack = f"{title} {summary}".casefold()
    includes = [str(item) for item in config.get("include_keywords", [])]
    excludes = [str(item) for item in config.get("exclude_keywords", [])]
    if includes and not any(_contains_keyword(haystack, item) for item in includes):
        return False
    return not any(_contains_keyword(haystack, item) for item in excludes)


def source_metadata(config: dict) -> dict[str, str]:
    return {
        "source_name": str(config["name"]),
        "collector_id": str(config["id"]),
        "publisher_id": document_source_id(config),
    }
