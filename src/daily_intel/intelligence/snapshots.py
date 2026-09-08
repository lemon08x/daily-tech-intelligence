from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from daily_intel.core.models import Document, Event

SHANGHAI = ZoneInfo("Asia/Shanghai")
PLACEHOLDER_HEADLINE = "待核验：来源材料"


def clip_text(value: str, max_chars: int) -> str:
    text = value or ""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars]


def stage_documents(documents: list[Document], max_chars: int) -> list[Document]:
    """Documents actually supplied to a model stage: clipped, not the DB original."""
    clipped: list[Document] = []
    for document in documents:
        source = document.content or document.summary
        clipped.append(document.model_copy(update={
            "content": clip_text(source, max_chars),
            "summary": clip_text(source, max_chars),
        }))
    return clipped


def snapshot_text(documents: list[Document]) -> str:
    return json.dumps([{
        "document_id": d.id, "url": d.url, "title": d.title,
        "content": d.content or d.summary,
    } for d in documents], ensure_ascii=False, sort_keys=True)


def snapshot_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def documents_snapshot_hash(documents: list[Document]) -> str:
    return snapshot_hash(snapshot_text(documents))


def content_fingerprint(documents: list[Document]) -> str:
    """Substantive body fingerprint; ignores fetched_at and whitespace-only edits."""
    chunks: list[str] = []
    for document in documents:
        title = _substantial(document.title)
        body = _substantial(document.content or document.summary)
        chunks.append(f"{title}\n{body}")
    return snapshot_hash("\n--\n".join(chunks))


def canonical_urls(documents: list[Document]) -> list[str]:
    urls = []
    seen: set[str] = set()
    for document in documents:
        url = str(document.canonical_url or document.url).rstrip("/")
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def quote_span(quote: str, source: str) -> tuple[int, int] | None:
    start = source.find(quote)
    if start < 0:
        return None
    return start, start + len(quote)


def _substantial(value: str) -> str:
    return re.sub(r"\s+", "", value or "").strip()


def aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def display_shanghai(value: datetime | None) -> str:
    current = aware(value)
    if current is None:
        return "日期未知"
    return current.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M")


def source_published_at(document: Document) -> datetime | None:
    return aware(document.published_at)


def original_published_at(document: Document) -> datetime | None:
    metadata = document.metadata or {}
    for key in (
        "original_published_at", "journal_published_at", "paper_published_at",
        "arxiv_published_at", "first_published_at",
    ):
        raw = metadata.get(key)
        parsed = _parse_datetime(raw)
        if parsed is not None:
            return parsed
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return aware(value)
    if not value:
        return None
    text = str(value).strip()
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return aware(datetime.fromisoformat(candidate))
        except ValueError:
            continue
    return None


def event_dates(event: Event, documents: list[Document]) -> dict[str, datetime | None]:
    originals = [original_published_at(item) for item in documents]
    published = [source_published_at(item) for item in documents]
    return {
        "source_published_at": min((item for item in published if item), default=None),
        "original_published_at": min((item for item in originals if item), default=None),
        "first_seen": aware(event.first_seen),
        "last_seen": aware(event.last_seen),
    }


def hours_since(value: datetime | None, now: datetime) -> float | None:
    current = aware(value)
    if current is None:
        return None
    return (aware(now) - current).total_seconds() / 3600.0
