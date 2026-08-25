from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from daily_intel.core.models import Document
from daily_intel.intelligence.clustering import cluster_documents
from daily_intel.intelligence.extraction import enrich_document
from daily_intel.intelligence.sources.feeds import ArxivSource, FeedSource


class FakeResponse:
    def __init__(self, content: bytes, text: str | None = None) -> None:
        self.content = content
        self.text = text if text is not None else content.decode("utf-8")

    def raise_for_status(self) -> None:
        return None


def test_atom_release_preserves_body(monkeypatch: pytest.MonkeyPatch) -> None:
    atom = b"""<?xml version='1.0' encoding='utf-8'?>
    <feed xmlns='http://www.w3.org/2005/Atom'>
      <entry><id>tag:github.com,2008:Release/1</id><title>v1.2 inference release</title>
      <updated>2026-08-24T01:00:00Z</updated><link href='https://github.com/acme/repo/releases/tag/v1.2'/>
      <content type='html'>&lt;p&gt;New inference architecture and benchmark details.&lt;/p&gt;</content></entry>
    </feed>"""
    monkeypatch.setattr("daily_intel.intelligence.sources.feeds.requests.get", lambda *a, **k: FakeResponse(atom))
    source = FeedSource({
        "id": "release", "name": "Release", "url": "https://github.com/acme/repo/releases.atom",
        "tier": 1, "fetch_full_text": False,
    })
    docs = source.collect(datetime(2026, 8, 23, tzinfo=timezone.utc), 30)
    assert len(docs) == 1
    assert docs[0].content_type == "github_release"
    assert "benchmark details" in docs[0].summary


def test_arxiv_atom_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    atom = b"""<?xml version='1.0' encoding='utf-8'?>
    <feed xmlns='http://www.w3.org/2005/Atom'>
      <entry><id>https://arxiv.org/abs/2608.12345v1</id><title>Robot foundation model</title>
      <published>2026-08-24T02:00:00Z</published><summary>A robotics evaluation method.</summary>
      <author><name>Alice</name></author><link href='https://arxiv.org/pdf/2608.12345v1' type='application/pdf'/></entry>
    </feed>"""
    monkeypatch.setattr("daily_intel.intelligence.sources.feeds.requests.get", lambda *a, **k: FakeResponse(atom))
    source = ArxivSource({"id": "arxiv", "name": "arXiv", "tier": 1, "categories": ["cs.RO"]})
    docs = source.collect(datetime(2026, 8, 23, tzinfo=timezone.utc), 10)
    assert docs[0].external_id == "2608.12345v1"
    assert docs[0].metadata["pdf_url"].endswith("2608.12345v1")


def _document(identifier: str, title: str, published: datetime) -> Document:
    return Document(
        id=identifier, source_id="primary", source_name="Primary", external_id=identifier,
        title=title, url=f"https://example.com/{identifier}", canonical_url=f"https://example.com/{identifier}",
        published_at=published, fetched_at=published, summary="robotics architecture benchmark method",
        content="robotics architecture benchmark method", content_hash=identifier * 8,
        source_tier=1, metadata={"source_name": "Primary"},
    )


def test_cluster_deduplicates_similar_titles_within_72_hours() -> None:
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    docs = [
        _document("a", "New robot foundation model released", now),
        _document("b", "Released: new robot foundation model", now - timedelta(hours=3)),
    ]
    topics = [{"id": "robotics", "name": "机器人", "keywords": ["robot"]}]
    events = cluster_documents(docs, topics, 72, 80)
    assert len(events) == 1
    assert len(events[0].document_ids) == 2


def test_cluster_excludes_obvious_nightly_release_noise() -> None:
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    noisy = _document("n", "trunk/68d20d4ee3956ceb: Disable CUDA arch", now).model_copy(
        update={"content_type": "github_release"}
    )
    topics = [{"id": "compute", "name": "芯片", "keywords": ["cuda"]}]
    assert cluster_documents([noisy], topics, 72, 80) == []


def test_full_text_failure_keeps_summary_and_marks_error(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _document("c", "Robot model", datetime.now(timezone.utc)).model_copy(
        update={"metadata": {"source_name": "Primary", "fetch_full_text": True}}
    )
    monkeypatch.setattr(
        "daily_intel.intelligence.extraction.requests.get",
        lambda *a, **k: (_ for _ in ()).throw(TimeoutError("timeout")),
    )
    result = enrich_document(doc, 1, 1000)
    assert result.extraction_quality == "summary"
    assert "TimeoutError" in result.metadata["extraction_error"]
