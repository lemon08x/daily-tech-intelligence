from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from daily_intel.core.models import Document
from daily_intel.intelligence.clustering import cluster_documents
from daily_intel.intelligence.extraction import enrich_document
from daily_intel.intelligence.sources.curated import HuggingFaceDailyPapersSource
from daily_intel.intelligence.sources.factory import build_sources, configured_source_count
from daily_intel.intelligence.sources.feeds import ArxivSource, FeedSource
from daily_intel.intelligence.sources.sitemaps import SitemapSource


class FakeResponse:
    def __init__(self, content: bytes, text: str | None = None) -> None:
        self.content = content
        self.text = text if text is not None else content.decode("utf-8")

    def raise_for_status(self) -> None:
        return None


class FakeJsonResponse(FakeResponse):
    def __init__(self, payload: object) -> None:
        super().__init__(b"")
        self.payload = payload

    def json(self) -> object:
        return self.payload


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


def test_feed_filters_noise_and_separates_collector_from_publisher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rss = b"""<?xml version='1.0' encoding='utf-8'?>
    <rss version='2.0'><channel><title>Hardware</title>
      <item><guid>deal</guid><title>GPU deal saves $500</title>
      <link>https://example.com/deal</link><pubDate>Mon, 24 Aug 2026 01:00:00 GMT</pubDate>
      <description>Lowest price on a gaming GPU.</description></item>
      <item><guid>architecture</guid><title>New GPU accelerator architecture</title>
      <link>https://example.com/architecture</link><pubDate>Mon, 24 Aug 2026 02:00:00 GMT</pubDate>
      <description>Semiconductor inference benchmark and HBM design.</description></item>
    </channel></rss>"""
    monkeypatch.setattr(
        "daily_intel.intelligence.sources.feeds.requests.get",
        lambda *a, **k: FakeResponse(rss),
    )
    source = FeedSource({
        "id": "hardware_radar", "publisher_id": "hardware_publisher",
        "name": "Hardware", "url": "https://example.com/feed.xml", "tier": 2,
        "include_keywords": ["GPU"], "exclude_keywords": ["deal", "lowest price"],
    })
    docs = source.collect(datetime(2026, 8, 23, tzinfo=timezone.utc), 30)
    assert [item.external_id for item in docs] == ["architecture"]
    assert docs[0].source_id == "hardware_publisher"
    assert docs[0].metadata["collector_id"] == "hardware_radar"


def test_sitemap_source_applies_date_and_path_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sitemap = b"""<?xml version='1.0' encoding='UTF-8'?>
    <urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
      <url><loc>https://example.com/news/claude-safety</loc><lastmod>2026-08-24T02:00:00Z</lastmod></url>
      <url><loc>https://example.com/research/team/economics</loc><lastmod>2026-08-24T03:00:00Z</lastmod></url>
      <url><loc>https://example.com/news/old-item</loc><lastmod>2026-08-01T03:00:00Z</lastmod></url>
    </urlset>"""
    monkeypatch.setattr(
        "daily_intel.intelligence.sources.sitemaps.requests.get",
        lambda *a, **k: FakeResponse(sitemap),
    )
    source = SitemapSource({
        "id": "official", "name": "Official", "url": "https://example.com/sitemap.xml",
        "tier": 1, "include_paths": ["/news/", "/research/"],
        "exclude_paths": ["/research/team/"],
    })
    docs = source.collect(datetime(2026, 8, 23, tzinfo=timezone.utc), 10)
    assert len(docs) == 1
    assert docs[0].title == "Claude safety"
    assert docs[0].extraction_quality == "metadata"
    assert docs[0].metadata["fetch_full_text"] is True


def test_sitemap_source_uses_official_page_metadata_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sitemap = b"""<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
      <url><loc>https://example.com/news/fable-5-s-safeguards</loc>
      <lastmod>2026-08-24T02:00:00Z</lastmod></url></urlset>"""
    page = b"""<html><head><title>Improving Fable 5 Safeguards</title>
      <meta name='description' content='An official biology safeguards update.'></head>
      <body><article>Detailed official update.</article></body></html>"""

    def get(url: str, **kwargs) -> FakeResponse:
        return FakeResponse(sitemap if url.endswith("sitemap.xml") else page)

    monkeypatch.setattr("daily_intel.intelligence.sources.sitemaps.requests.get", get)
    source = SitemapSource({
        "id": "official", "name": "Official", "url": "https://example.com/sitemap.xml",
        "tier": 1, "include_paths": ["/news/"], "fetch_page_metadata": True,
    })
    docs = source.collect(datetime(2026, 8, 23, tzinfo=timezone.utc), 10)
    assert docs[0].title == "Improving Fable 5 Safeguards"
    assert docs[0].summary == "An official biology safeguards update."


def test_huggingface_daily_papers_cites_original_arxiv_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = [{
        "paper": {
            "id": "2608.12345", "title": "A code agent benchmark",
            "summary": "A software engineering evaluation method.",
            "publishedAt": "2026-08-22T00:00:00.000Z",
            "submittedOnDailyAt": "2026-08-24T00:00:00.000Z",
            "authors": [{"name": "Alice"}], "upvotes": 12,
        }
    }]
    monkeypatch.setattr(
        "daily_intel.intelligence.sources.curated.requests.get",
        lambda *a, **k: FakeJsonResponse(payload),
    )
    source = HuggingFaceDailyPapersSource({
        "id": "hf_daily", "publisher_id": "arxiv", "name": "HF Daily",
        "tier": 1,
    })
    docs = source.collect(datetime(2026, 8, 23, tzinfo=timezone.utc), 10)
    assert len(docs) == 1
    assert docs[0].source_id == "arxiv"
    assert docs[0].url == "https://arxiv.org/abs/2608.12345"
    assert docs[0].metadata["discovery_url"] == "https://huggingface.co/papers/2608.12345"
    assert docs[0].metadata["pdf_url"].endswith("2608.12345")


def test_source_factory_supports_grouped_arxiv_sitemaps_apis_and_legacy_arxiv() -> None:
    config = {
        "arxiv_sources": [{
            "id": "arxiv_ai", "publisher_id": "arxiv", "name": "arXiv AI",
            "tier": 1, "categories": ["cs.AI"],
        }],
        "feeds": [{
            "id": "official_feed", "name": "Official", "tier": 1,
            "url": "https://example.com/feed.xml",
        }],
        "sitemaps": [{
            "id": "official_map", "name": "Map", "tier": 1,
            "url": "https://example.com/sitemap.xml",
        }],
        "apis": [{
            "id": "daily", "type": "huggingface_daily_papers",
            "name": "Daily", "tier": 1,
        }],
        "github_releases": [{
            "id": "release", "name": "Release", "tier": 1, "repo": "acme/repo",
        }],
    }
    sources = build_sources(config, 5)
    assert configured_source_count(config) == 5
    assert {type(item).__name__ for item in sources} == {
        "ArxivSource", "FeedSource", "SitemapSource", "HuggingFaceDailyPapersSource",
    }
    legacy = build_sources({
        "arxiv": {"id": "old", "name": "Old", "tier": 1, "categories": ["cs.RO"]},
    }, 5)
    assert len(legacy) == 1 and isinstance(legacy[0], ArxivSource)


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
