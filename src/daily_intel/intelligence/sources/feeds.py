from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from time import struct_time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import requests

from daily_intel.core.models import Document


USER_AGENT = "DailyIntel/0.2 (+local research digest)"


def _as_datetime(value: struct_time | None, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    return datetime(*value[:6], tzinfo=timezone.utc)


def _plain_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", html.unescape(value or ""))
    return re.sub(r"\s+", " ", value).strip()


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), ""))


def _document_id(source_id: str, external_id: str) -> str:
    return hashlib.sha256(f"{source_id}\0{external_id}".encode()).hexdigest()[:24]


def _content_hash(title: str, content: str) -> str:
    normalized = re.sub(r"\W+", "", f"{title}{content}").lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class FeedSource:
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
        documents: list[Document] = []
        for entry in feed.entries:
            published = _as_datetime(
                entry.get("published_parsed") or entry.get("updated_parsed"), now
            )
            if published < since.astimezone(timezone.utc):
                continue
            title = _plain_text(entry.get("title", ""))
            url = str(entry.get("link", ""))
            external_id = str(entry.get("id") or url or title)
            content_blocks = entry.get("content") or []
            content_value = content_blocks[0].get("value", "") if content_blocks else ""
            summary = _plain_text(
                entry.get("summary") or entry.get("description") or content_value
            )
            if not title or not url:
                continue
            metadata = {
                "source_name": self.config["name"],
                "feed_url": self.config["url"],
                "fetch_full_text": bool(self.config.get("fetch_full_text", False)),
            }
            document = Document(
                id=_document_id(self.source_id, external_id), source_id=self.source_id,
                source_name=str(self.config["name"]), external_id=external_id, title=title,
                url=url, canonical_url=canonicalize_url(url), published_at=published,
                fetched_at=now, summary=summary, content=summary,
                content_hash=_content_hash(title, summary), source_tier=int(self.config["tier"]),
                content_type="github_release" if "github.com" in self.config["url"] else "article",
                extraction_quality="summary", metadata=metadata,
            )
            documents.append(document)
            if len(documents) >= limit:
                break
        return documents


class ArxivSource:
    def __init__(self, config: dict, timeout: int = 20) -> None:
        self.config = config
        self.source_id = str(config["id"])
        self.timeout = timeout

    def collect(self, since: datetime, limit: int) -> list[Document]:
        query = "+OR+".join(f"cat:{item}" for item in self.config["categories"])
        url = (
            "https://export.arxiv.org/api/query?search_query=" + query
            + f"&start=0&max_results={limit}&sortBy=submittedDate&sortOrder=descending"
        )
        response = requests.get(url, timeout=self.timeout, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        now = datetime.now(timezone.utc)
        documents: list[Document] = []
        for entry in feed.entries:
            published = _as_datetime(entry.get("published_parsed"), now)
            if published < since.astimezone(timezone.utc):
                continue
            canonical = str(entry.get("id", ""))
            arxiv_id = canonical.rstrip("/").split("/")[-1]
            pdf_url = next(
                (link.href for link in entry.get("links", []) if getattr(link, "type", "") == "application/pdf"),
                canonical.replace("/abs/", "/pdf/") + ".pdf",
            )
            title = _plain_text(entry.get("title", ""))
            summary = _plain_text(entry.get("summary", ""))
            metadata = {
                "source_name": self.config["name"], "arxiv_id": arxiv_id,
                "pdf_url": pdf_url, "authors": [a.get("name", "") for a in entry.get("authors", [])],
                "fetch_full_text": True,
            }
            documents.append(
                Document(
                    id=_document_id(self.source_id, arxiv_id), source_id=self.source_id,
                    source_name=str(self.config["name"]), external_id=arxiv_id, title=title,
                    url=canonical, canonical_url=canonicalize_url(canonical), published_at=published,
                    fetched_at=now, summary=summary, content=summary,
                    content_hash=_content_hash(title, summary), source_tier=int(self.config["tier"]),
                    content_type="paper", extraction_quality="summary", metadata=metadata,
                )
            )
        return documents


def build_sources(config: dict, timeout: int) -> list[FeedSource | ArxivSource]:
    sources: list[FeedSource | ArxivSource] = []
    arxiv = config.get("arxiv", {})
    if arxiv.get("enabled", True):
        sources.append(ArxivSource(arxiv, timeout))
    for feed in config.get("feeds", []):
        if feed.get("enabled", True):
            sources.append(FeedSource(feed, timeout))
    for item in config.get("github_releases", []):
        if not item.get("enabled", True):
            continue
        feed_config = {
            **item,
            "url": f"https://github.com/{item['repo']}/releases.atom",
            "fetch_full_text": False,
        }
        sources.append(FeedSource(feed_config, timeout))
    return sources
