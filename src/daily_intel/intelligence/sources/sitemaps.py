from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

import requests
import trafilatura

from daily_intel.core.models import Document
from daily_intel.intelligence.sources.common import (
    USER_AGENT,
    canonicalize_url,
    content_hash,
    document_id,
    document_source_id,
    effective_limit,
    parse_iso_datetime,
    passes_keyword_filters,
    plain_text,
    source_metadata,
)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _slug_title(url: str) -> str:
    slug = unquote(urlsplit(url).path.rstrip("/").split("/")[-1])
    words = re.sub(r"[-_]+", " ", slug).strip()
    return words[:1].upper() + words[1:] if words else url


class SitemapSource:
    """Discover dated official articles when a publisher has no stable RSS feed."""

    def __init__(self, config: dict, timeout: int = 20) -> None:
        self.config = config
        self.source_id = str(config["id"])
        self.timeout = timeout

    def collect(self, since: datetime, limit: int) -> list[Document]:
        response = requests.get(
            self.config["url"], timeout=self.timeout, headers={"User-Agent": USER_AGENT}
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
        if _local_name(root.tag) != "urlset":
            raise ValueError("Only sitemap urlset documents are supported")

        include_paths = [str(item) for item in self.config.get("include_paths", [])]
        exclude_paths = [str(item) for item in self.config.get("exclude_paths", [])]
        candidates: list[tuple[datetime, str]] = []
        for element in root:
            if _local_name(element.tag) != "url":
                continue
            values = {_local_name(child.tag): (child.text or "").strip() for child in element}
            url = values.get("loc", "")
            path = urlsplit(url).path
            if not url or (include_paths and not any(path.startswith(item) for item in include_paths)):
                continue
            if any(path.startswith(item) for item in exclude_paths):
                continue
            modified = parse_iso_datetime(values.get("lastmod"))
            if modified is None or modified < since.astimezone(timezone.utc):
                continue
            candidates.append((modified, url))

        now = datetime.now(timezone.utc)
        document_source = document_source_id(self.config)
        source_limit = effective_limit(self.config, limit)
        documents: list[Document] = []
        for modified, url in sorted(candidates, reverse=True):
            title = _slug_title(url)
            summary = ""
            content = ""
            extraction_quality = "metadata"
            metadata_error = ""
            if self.config.get("fetch_page_metadata", False):
                try:
                    page = requests.get(
                        url, timeout=self.timeout, headers={"User-Agent": USER_AGENT}
                    )
                    page.raise_for_status()
                    extracted = trafilatura.extract_metadata(page.text)
                    if extracted is not None:
                        title = plain_text(extracted.title or title)
                        summary = plain_text(extracted.description or "")
                    if self.config.get("fetch_full_text", True):
                        full_text = trafilatura.extract(
                            page.text,
                            include_links=False,
                            include_images=False,
                            include_comments=False,
                        ) or ""
                        full_text = full_text.strip()
                        if len(full_text) >= 200:
                            content = full_text
                            extraction_quality = "full"
                except Exception as exc:
                    metadata_error = f"{type(exc).__name__}: {exc}"[:300]
            if not passes_keyword_filters(title, summary, self.config):
                continue
            canonical = canonicalize_url(url)
            metadata = {
                **source_metadata(self.config),
                "sitemap_url": self.config["url"],
                "fetch_full_text": bool(self.config.get("fetch_full_text", True)),
            }
            path_topic_hints = self.config.get("path_topic_hints") or {}
            for prefix, topic_id in path_topic_hints.items():
                if urlsplit(url).path.startswith(str(prefix)):
                    metadata["topic_hint"] = str(topic_id)
                    break
            if metadata_error:
                metadata["metadata_error"] = metadata_error
            documents.append(
                Document(
                    id=document_id(document_source, canonical),
                    source_id=document_source,
                    source_name=str(self.config["name"]),
                    external_id=canonical,
                    title=title,
                    url=url,
                    canonical_url=canonical,
                    published_at=modified,
                    fetched_at=now,
                    summary=summary or title,
                    content=content or summary or title,
                    content_hash=content_hash(title, summary or canonical),
                    source_tier=int(self.config["tier"]),
                    content_type="article",
                    extraction_quality=extraction_quality,
                    metadata=metadata,
                )
            )
            if len(documents) >= source_limit:
                break
        return documents
