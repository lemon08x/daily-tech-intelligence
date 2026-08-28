from __future__ import annotations

from datetime import datetime, timezone

import feedparser
import requests

from daily_intel.core.models import Document
from daily_intel.intelligence.sources.common import (
    USER_AGENT,
    as_datetime,
    canonicalize_url,
    content_hash,
    document_id,
    document_lane,
    document_source_id,
    effective_limit,
    extract_http_urls,
    passes_keyword_filters,
    plain_text,
    resolve_public_url,
    should_unshorten,
    source_metadata,
)


class FeedSource:
    """Collect a configured RSS or Atom feed without leaking later pipeline concerns."""

    def __init__(self, config: dict, timeout: int = 20) -> None:
        self.config = config
        self.source_id = str(config["id"])
        self.timeout = timeout

    def collect(self, since: datetime, limit: int) -> list[Document]:
        response = requests.get(
            self.config["url"], timeout=self.timeout, headers={"User-Agent": USER_AGENT}
        )
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        if getattr(feed, "bozo", False) and not feed.entries:
            raise ValueError(f"Feed parse failed: {feed.bozo_exception}")
        now = datetime.now(timezone.utc)
        document_source = document_source_id(self.config)
        source_limit = effective_limit(self.config, limit)
        documents: list[Document] = []
        for entry in feed.entries:
            published = as_datetime(
                entry.get("published_parsed") or entry.get("updated_parsed"), now
            )
            if published < since.astimezone(timezone.utc):
                continue
            title = plain_text(entry.get("title", ""))
            url = str(entry.get("link", ""))
            external_id = str(entry.get("id") or url or title)
            content_blocks = entry.get("content") or []
            content_value = content_blocks[0].get("value", "") if content_blocks else ""
            summary = plain_text(
                entry.get("summary") or entry.get("description") or content_value
            )
            if (
                not title
                or not url
                or not passes_keyword_filters(title, summary, self.config)
            ):
                continue
            content_type = str(
                self.config.get(
                    "content_type",
                    "github_release" if "/releases" in self.config["url"] else "article",
                )
            )
            public_url = (
                resolve_public_url(url, min(self.timeout, 12))
                if should_unshorten(url, self.config)
                else url
            )
            target_url = ""
            for candidate in extract_http_urls(f"{title} {summary}"):
                if "github.com/ruanyf/weekly" in candidate.lower():
                    continue
                target_url = (
                    resolve_public_url(candidate, min(self.timeout, 12))
                    if should_unshorten(candidate, self.config)
                    else canonicalize_url(candidate)
                )
                if target_url:
                    break
            metadata = {
                **source_metadata(self.config),
                "feed_url": self.config["url"],
                "fetch_full_text": bool(self.config.get("fetch_full_text", False)),
                "lane": document_lane(self.config, content_type),
            }
            if target_url:
                metadata["target_url"] = target_url
            documents.append(
                Document(
                    id=document_id(document_source, external_id),
                    source_id=document_source,
                    source_name=str(self.config["name"]),
                    external_id=external_id,
                    title=title,
                    url=public_url,
                    canonical_url=canonicalize_url(public_url),
                    published_at=published,
                    fetched_at=now,
                    summary=summary,
                    content=summary,
                    content_hash=content_hash(title, summary),
                    source_tier=int(self.config["tier"]),
                    content_type=content_type,
                    extraction_quality="summary",
                    metadata=metadata,
                )
            )
            if len(documents) >= source_limit:
                break
        return documents


class ArxivSource:
    """Collect one bounded arXiv category group using a shared publisher identity."""

    def __init__(self, config: dict, timeout: int = 20) -> None:
        self.config = config
        self.source_id = str(config["id"])
        self.timeout = timeout

    def collect(self, since: datetime, limit: int) -> list[Document]:
        source_limit = effective_limit(self.config, limit)
        query = "+OR+".join(f"cat:{item}" for item in self.config["categories"])
        url = (
            "https://export.arxiv.org/api/query?search_query="
            + query
            + f"&start=0&max_results={source_limit}&sortBy=submittedDate&sortOrder=descending"
        )
        response = requests.get(url, timeout=self.timeout, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        now = datetime.now(timezone.utc)
        document_source = document_source_id(self.config)
        documents: list[Document] = []
        for entry in feed.entries:
            published = as_datetime(entry.get("published_parsed"), now)
            if published < since.astimezone(timezone.utc):
                continue
            canonical = str(entry.get("id", ""))
            arxiv_id = canonical.rstrip("/").split("/")[-1]
            pdf_url = next(
                (
                    link.href
                    for link in entry.get("links", [])
                    if getattr(link, "type", "") == "application/pdf"
                ),
                canonical.replace("/abs/", "/pdf/") + ".pdf",
            )
            title = plain_text(entry.get("title", ""))
            summary = plain_text(entry.get("summary", ""))
            if not passes_keyword_filters(title, summary, self.config):
                continue
            metadata = {
                **source_metadata(self.config),
                "arxiv_id": arxiv_id,
                "pdf_url": pdf_url,
                "authors": [a.get("name", "") for a in entry.get("authors", [])],
                "categories": list(self.config["categories"]),
                "fetch_full_text": True,
            }
            documents.append(
                Document(
                    id=document_id(document_source, arxiv_id),
                    source_id=document_source,
                    source_name=str(self.config["name"]),
                    external_id=arxiv_id,
                    title=title,
                    url=canonical,
                    canonical_url=canonicalize_url(canonical),
                    published_at=published,
                    fetched_at=now,
                    summary=summary,
                    content=summary,
                    content_hash=content_hash(title, summary),
                    source_tier=int(self.config["tier"]),
                    content_type="paper",
                    extraction_quality="summary",
                    metadata=metadata,
                )
            )
        return documents
