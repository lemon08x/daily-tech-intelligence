from __future__ import annotations

from datetime import datetime, timezone

import requests

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


class HuggingFaceDailyPapersSource:
    """Use Hugging Face curation for discovery while citing the original arXiv paper."""

    DEFAULT_URL = "https://huggingface.co/api/daily_papers"

    def __init__(self, config: dict, timeout: int = 20) -> None:
        self.config = config
        self.source_id = str(config["id"])
        self.timeout = timeout

    def collect(self, since: datetime, limit: int) -> list[Document]:
        response = requests.get(
            self.config.get("url", self.DEFAULT_URL),
            timeout=self.timeout,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Hugging Face Daily Papers returned a non-list payload")

        now = datetime.now(timezone.utc)
        document_source = document_source_id(self.config)
        source_limit = effective_limit(self.config, limit)
        documents: list[Document] = []
        for item in payload:
            paper = item.get("paper") or item
            arxiv_id = str(paper.get("id") or "").strip()
            if not arxiv_id:
                continue
            discovered_at = parse_iso_datetime(
                paper.get("submittedOnDailyAt")
                or item.get("submittedOnDailyAt")
                or item.get("publishedAt")
                or paper.get("publishedAt"),
                now,
            )
            if discovered_at is None or discovered_at < since.astimezone(timezone.utc):
                continue
            title = plain_text(paper.get("title") or item.get("title") or "")
            summary = plain_text(paper.get("summary") or item.get("summary") or "")
            if not title or not passes_keyword_filters(title, summary, self.config):
                continue
            canonical = f"https://arxiv.org/abs/{arxiv_id}"
            metadata = {
                **source_metadata(self.config),
                "arxiv_id": arxiv_id,
                "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
                "authors": [author.get("name", "") for author in paper.get("authors", [])],
                "original_published_at": paper.get("publishedAt", ""),
                "discovery_url": f"https://huggingface.co/papers/{arxiv_id}",
                "upvotes": int(paper.get("upvotes") or 0),
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
                    published_at=discovered_at,
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
            if len(documents) >= source_limit:
                break
        return documents
