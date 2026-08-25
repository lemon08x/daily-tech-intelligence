from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import pandas as pd

from daily_intel.core.models import Document
from daily_intel.core.ports import IntelligenceRepository, SourceAdapter
from daily_intel.intelligence.sources.common import (
    canonicalize_url,
    content_hash,
    document_id,
)
from daily_intel.intelligence.sources.factory import build_sources


SourceFactory = Callable[[dict[str, Any], int], list[SourceAdapter]]


class DocumentCollector:
    """Collects authoritative sources and market radar without knowing later AI stages."""

    def __init__(
        self, settings: dict[str, Any], repository: IntelligenceRepository,
        source_factory: SourceFactory = build_sources,
    ) -> None:
        self.settings = settings
        self.config = settings["intelligence"]
        self.repository = repository
        self.source_factory = source_factory

    def collection_since(self, now: datetime, source_id: str) -> datetime:
        state = self.repository.get_state(f"source_cursor:{source_id}")
        if not state:
            state = self.repository.get_state("last_collect_at")
        if not state:
            return now.astimezone(timezone.utc) - timedelta(
                hours=int(self.config["first_run_lookback_hours"])
            )
        previous = datetime.fromisoformat(state)
        return previous.astimezone(timezone.utc) - timedelta(
            hours=int(self.config["resume_overlap_hours"])
        )

    def collect_sources(self, now: datetime) -> tuple[list[Document], list[dict[str, Any]]]:
        sources = self.source_factory(
            self.settings["sources"], int(self.config["source_fetch_timeout_seconds"])
        )
        documents: list[Document] = []
        statuses: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(
                    source.collect,
                    self.collection_since(now, source.source_id),
                    int(self.config["max_items_per_source"]),
                ): source
                for source in sources
            }
            for future in as_completed(futures):
                source = futures[future]
                source_name = getattr(source, "config", {}).get("name", source.source_id)
                try:
                    items = future.result()
                    documents.extend(items)
                    self.repository.set_state(f"source_cursor:{source.source_id}", now.isoformat())
                    statuses.append({
                        "name": source.source_id,
                        "source": source_name,
                        "fetched_at": now.isoformat(timespec="seconds"),
                        "stale": False,
                        "count": len(items),
                        "error": "",
                    })
                except Exception as exc:
                    statuses.append({
                        "name": source.source_id,
                        "source": source_name,
                        "fetched_at": "",
                        "stale": True,
                        "count": 0,
                        "error": f"{type(exc).__name__}: {exc}"[:300],
                    })
        return documents, sorted(statuses, key=lambda item: item["name"])

    def radar_documents(
        self, news: pd.DataFrame, now: datetime, source_id: str = "market_radar",
    ) -> list[Document]:
        documents: list[Document] = []
        since = self.collection_since(now, source_id)
        if news.empty:
            self.repository.set_state(f"source_cursor:{source_id}", now.isoformat())
            return documents
        for index, row in news.head(30).iterrows():
            published = pd.to_datetime(row.get("published_at"), errors="coerce")
            if pd.isna(published):
                published_at = now
            else:
                timestamp = pd.Timestamp(published)
                timestamp = (
                    timestamp.tz_localize(now.tzinfo)
                    if timestamp.tzinfo is None
                    else timestamp.tz_convert(now.tzinfo)
                )
                published_at = timestamp.to_pydatetime()
            if published_at.astimezone(timezone.utc) < since.astimezone(timezone.utc):
                continue
            title, summary = str(row.get("title", "")), str(row.get("summary", ""))
            url = str(row.get("url", "")) or f"radar://{index}"
            external_id = url if not url.startswith("radar://") else f"{published_at.isoformat()}:{title}"
            documents.append(Document(
                id=document_id(source_id, external_id),
                source_id=source_id,
                source_name="同花顺/新浪快讯线索",
                external_id=external_id,
                title=title,
                url=url,
                canonical_url=canonicalize_url(url),
                published_at=published_at,
                fetched_at=now,
                summary=summary,
                content=summary,
                content_hash=content_hash(title, summary),
                source_tier=3,
                content_type="news_radar",
                extraction_quality="summary",
                metadata={"source_name": "同花顺/新浪快讯线索"},
            ))
        self.repository.set_state(f"source_cursor:{source_id}", now.isoformat())
        return documents
