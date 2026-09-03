from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from daily_intel.core.models import Document, Event
from daily_intel.intelligence.clustering import UNCLASSIFIED_TOPIC_ID, cluster_documents
from daily_intel.intelligence.selection import EventSelector
from daily_intel.intelligence.sources.common import event_lane
from daily_intel.intelligence.extraction import enrich_document
from daily_intel.intelligence.sources.curated import HuggingFaceDailyPapersSource
from daily_intel.intelligence.sources.factory import build_sources, configured_source_count
from daily_intel.intelligence.sources.common import canonicalize_url, document_lane, resolve_public_url
from daily_intel.intelligence.sources.feeds import ArxivSource, FeedSource
from daily_intel.intelligence.sources.github_issues import GitHubIssuesSource
from daily_intel.intelligence.sources.weekly_catalog import parse_weekly_markdown
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
      <url><loc>https://example.com/claude-fable-5-1</loc><lastmod>2026-08-24T02:30:00Z</lastmod></url>
      <url><loc>https://example.com/research/team/economics</loc><lastmod>2026-08-24T03:00:00Z</lastmod></url>
      <url><loc>https://example.com/news/old-item</loc><lastmod>2026-08-01T03:00:00Z</lastmod></url>
    </urlset>"""
    monkeypatch.setattr(
        "daily_intel.intelligence.sources.sitemaps.requests.get",
        lambda *a, **k: FakeResponse(sitemap),
    )
    source = SitemapSource({
        "id": "official", "name": "Official", "url": "https://example.com/sitemap.xml",
        "tier": 1, "include_paths": ["/news/", "/research/", "/claude-"],
        "path_topic_hints": {"/claude-": "foundation_models"},
        "exclude_paths": ["/research/team/"],
    })
    docs = source.collect(datetime(2026, 8, 23, tzinfo=timezone.utc), 10)
    assert len(docs) == 2
    assert {item.title for item in docs} == {"Claude safety", "Claude fable 5 1"}
    assert docs[0].extraction_quality == "metadata"
    assert docs[0].metadata["fetch_full_text"] is True
    fable = next(item for item in docs if "fable" in item.url)
    assert fable.metadata["topic_hint"] == "foundation_models"


def test_sitemap_source_reuses_page_fetch_for_early_full_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sitemap = b"""<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
      <url><loc>https://example.com/claude-release</loc><lastmod>2026-08-24T02:00:00Z</lastmod></url>
    </urlset>"""
    page = "<html><head><title>Claude release</title></head><body><article><p>" + (
        "Detailed model architecture and coding capability evidence. " * 12
    ) + "</p></article></body></html>"

    def get(url: str, **kwargs) -> FakeResponse:
        return FakeResponse(sitemap) if url.endswith("sitemap.xml") else FakeResponse(page.encode(), page)

    monkeypatch.setattr("daily_intel.intelligence.sources.sitemaps.requests.get", get)
    source = SitemapSource({
        "id": "official", "name": "Official", "url": "https://example.com/sitemap.xml",
        "tier": 1, "include_paths": ["/claude-"], "fetch_page_metadata": True,
        "fetch_full_text": True,
    })
    docs = source.collect(datetime(2026, 8, 23, tzinfo=timezone.utc), 10)
    assert docs[0].extraction_quality == "full"
    assert "Detailed model architecture" in docs[0].content


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


def test_source_factory_supports_configured_source_types() -> None:
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
        "apis": [
            {
                "id": "daily", "type": "huggingface_daily_papers",
                "name": "Daily", "tier": 1,
            },
            {
                "id": "issues", "type": "github_issues", "repo": "acme/demo",
                "name": "Issues", "tier": 3,
            },
        ],
        "github_releases": [{
            "id": "release", "name": "Release", "tier": 1, "repo": "acme/repo",
        }],
    }
    sources = build_sources(config, 5)
    assert configured_source_count(config) == 6
    assert {type(item).__name__ for item in sources} == {
        "ArxivSource", "FeedSource", "SitemapSource", "HuggingFaceDailyPapersSource",
        "GitHubIssuesSource",
    }
    assert build_sources({
        "arxiv": {"id": "ignored", "name": "Ignored", "tier": 1, "categories": ["cs.RO"]},
    }, 5) == []


def test_github_issues_source_collects_each_new_issue_and_skips_pull_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    class Response:
        status_code = 200
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict]:
            return [
                {
                    "number": 11465,
                    "title": "开源 AI 日志分析工具",
                    "body": "项目地址 https://example.com/log-agent ，可分析服务器日志。",
                    "html_url": "https://github.com/ruanyf/weekly/issues/11465",
                    "created_at": "2026-09-02T01:30:00Z",
                    "state": "open",
                    "labels": [{"name": "投稿"}, {"name": "AI"}],
                    "user": {"login": "alice"},
                    "comments": 2,
                },
                {
                    "number": 11464,
                    "title": "Pull request",
                    "body": "not an issue",
                    "html_url": "https://github.com/ruanyf/weekly/pull/11464",
                    "created_at": "2026-09-02T01:00:00Z",
                    "pull_request": {"url": "https://api.github.com/pulls/11464"},
                },
                {
                    "number": 11463,
                    "title": "数据库可视化工具",
                    "body": "先看仓库说明 https://github.com/ruanyf/weekly/issues/11463 ，"
                            "项目主页 https://example.org/db-viewer 。",
                    "html_url": "https://github.com/ruanyf/weekly/issues/11463",
                    "created_at": "2026-09-02T00:30:00Z",
                    "state": "closed",
                    "labels": [],
                    "user": {"login": "bob"},
                    "comments": 0,
                },
                {
                    "number": 10000,
                    "title": "更新过的旧 Issue",
                    "body": "old",
                    "html_url": "https://github.com/ruanyf/weekly/issues/10000",
                    "created_at": "2026-08-01T00:00:00Z",
                    "state": "open",
                },
            ]

    def get(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr("daily_intel.intelligence.sources.github_issues.requests.get", get)
    source = GitHubIssuesSource({
        "id": "ruanyf_weekly_issues",
        "type": "github_issues",
        "repo": "ruanyf/weekly",
        "publisher_id": "ruanyf_weekly",
        "name": "阮一峰周刊投稿池",
        "tier": 3,
        "lane": "general",
        "content_type": "weekly_issue",
        "max_items": 30,
    })
    docs = source.collect(datetime(2026, 9, 2, tzinfo=timezone.utc), 30)

    assert [item.external_id for item in docs] == [
        "ruanyf/weekly#11465", "ruanyf/weekly#11463",
    ]
    assert all(item.source_id == "ruanyf_weekly" for item in docs)
    assert all(item.content_type == "weekly_issue" for item in docs)
    assert docs[0].metadata["labels"] == ["投稿", "AI"]
    assert docs[0].metadata["author"] == "alice"
    assert docs[0].metadata["target_url"] == "https://example.com/log-agent"
    assert docs[1].metadata["target_url"] == "https://example.org/db-viewer"
    assert captured["url"] == "https://api.github.com/repos/ruanyf/weekly/issues"
    assert captured["params"]["state"] == "all"
    assert captured["params"]["per_page"] == 100


def test_github_issues_source_explains_anonymous_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        status_code = 403
        headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "123456"}

        def raise_for_status(self) -> None:
            raise AssertionError("rate limit should be explained first")

    monkeypatch.setattr(
        "daily_intel.intelligence.sources.github_issues.requests.get",
        lambda *args, **kwargs: Response(),
    )
    source = GitHubIssuesSource({
        "id": "issues", "type": "github_issues", "repo": "acme/demo",
        "name": "Issues", "tier": 3,
    })
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        source.collect(datetime(2026, 9, 2, tzinfo=timezone.utc), 30)


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


def test_cluster_keeps_unclassified_official_release_for_scout() -> None:
    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    fable = _document("f", "Claude Fable 5.1 and Claude Mythos 5.1", now).model_copy(
        update={"summary": "Advanced systems for coding and knowledge work."}
    )
    topics = [{"id": "foundation_models", "name": "大模型", "keywords": ["llm", "agent"]}]
    events = cluster_documents([fable], topics, 72, 88)
    assert len(events) == 1
    assert events[0].topic_id == UNCLASSIFIED_TOPIC_ID


def test_cluster_merges_same_url_even_when_rule_topics_differ() -> None:
    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    official = _document("o", "Introducing Claude Fable 5.1", now).model_copy(update={
        "canonical_url": "https://example.com/fable",
        "url": "https://example.com/fable",
        "metadata": {"topic_hint": "foundation_models"},
    })
    discussion = _document("h", "Claude Fable 5.1 discussion", now).model_copy(update={
        "canonical_url": "https://example.com/fable",
        "url": "https://example.com/fable",
        "summary": "Community discussion without configured topic words.",
    })
    topics = [{"id": "foundation_models", "name": "大模型", "keywords": ["llm"]}]
    events = cluster_documents([official, discussion], topics, 72, 88)
    assert len(events) == 1
    assert set(events[0].document_ids) == {"o", "h"}
    assert events[0].topic_id == "foundation_models"


def test_cluster_merges_same_github_project_from_weekly_and_release() -> None:
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    weekly = _document("w", "A new vLLM serving trick", now).model_copy(update={
        "url": "https://github.com/ruanyf/weekly/issues/123",
        "canonical_url": "https://github.com/ruanyf/weekly/issues/123",
        "source_tier": 3,
        "metadata": {"lane": "general", "target_url": "https://github.com/vllm-project/vllm"},
    })
    release = _document("r", "vLLM v0.27.0", now).model_copy(update={
        "url": "https://github.com/vllm-project/vllm/releases/tag/v0.27.0",
        "canonical_url": "https://github.com/vllm-project/vllm/releases/tag/v0.27.0",
        "content_type": "github_release",
        "source_tier": 1,
        "metadata": {"lane": "hardcore"},
    })
    topics = [
        {"id": "cloud_devtools", "name": "云与开发工具", "keywords": ["vllm", "serving"]},
        {"id": "foundation_models", "name": "大模型", "keywords": ["vllm"]},
    ]
    events = cluster_documents([weekly, release], topics, 72, 80)
    assert len(events) == 1
    assert set(events[0].document_ids) == {"w", "r"}


def test_weekly_markdown_extracts_tool_and_resource_links() -> None:
    text = """
## 资源
1、[Cool Blog](https://example.com/post)

## 工具
1、[vLLM](https://github.com/vllm-project/vllm)

## 文摘
1、[Random tweet](https://twitter.com/foo/status/1)
"""
    rows = parse_weekly_markdown(text, "issue-1.md")
    urls = {item["url"] for item in rows}
    assert "https://example.com/post" in urls
    assert "https://github.com/vllm-project/vllm" in urls
    assert all("twitter.com" not in item["url"] for item in rows)


def test_short_url_resolves_to_canonical_target(monkeypatch: pytest.MonkeyPatch) -> None:
    class Redirect:
        url = "https://github.com/vllm-project/vllm/releases/tag/v1"
        def raise_for_status(self) -> None:
            return None
    monkeypatch.setattr("daily_intel.infrastructure.http.http_get", lambda *a, **k: Redirect())
    assert resolve_public_url("https://bit.ly/vllm") == canonicalize_url(
        "https://github.com/vllm-project/vllm/releases/tag/v1"
    )
    assert document_lane({"tier": 3, "lane": "general"}, "weekly_issue") == "general"
    assert document_lane({"tier": 1}, "paper") == "hardcore"


def test_soft_research_preferences_keep_general_and_backfill() -> None:
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)

    def pair(identifier: str, title: str, *, paper: bool) -> tuple[Event, list[Document]]:
        doc = _document(identifier, title, now).model_copy(update={
            "content_type": "paper" if paper else "article",
            "source_tier": 1 if paper else 2,
            "metadata": {"lane": "hardcore" if paper else "general"},
        })
        return Event(
            id=identifier, title=title, topic_id="compute", topic_name="芯片",
            document_ids=[doc.id], first_seen=now, last_seen=now,
            source_quality=90 if paper else 50, deterministic_score=90 if paper else 40,
        ), [doc]

    papers = [pair(f"p{index}", f"Paper {index} architecture", paper=True) for index in range(5)]
    weekly = pair("w1", "Weekly AI tool roundup", paper=False)
    selected = EventSelector.prioritize_for_research(
        [*papers, weekly], preferred_general=1, preferred_hardcore=2,
        preferred_max_per_topic=2,
    )
    lanes = [event_lane(docs) for _, docs in selected[:3]]
    assert lanes.count("general") == 1
    assert lanes.count("hardcore") == 2
    assert "w1" in {event.id for event, _ in selected[:3]}
    assert len(selected) == 6


def test_official_release_priority_bypasses_soft_topic_cap() -> None:
    now = datetime(2026, 9, 2, tzinfo=timezone.utc)

    def pair(identifier: str, title: str) -> tuple[Event, list[Document]]:
        doc = _document(identifier, title, now)
        return Event(
            id=identifier, title=title, topic_id="foundation_models", topic_name="大模型",
            document_ids=[doc.id], first_seen=now, last_seen=now,
            source_quality=100, deterministic_score=60,
        ), [doc]

    paper = pair("paper", "An LLM benchmark paper")
    release = pair("release", "Introducing Model 5.1")
    ordered = EventSelector.prioritize_for_research(
        [paper, release], preferred_general=0, preferred_hardcore=1,
        preferred_max_per_topic=1, priority_event_ids={"release"},
    )
    assert [item[0].id for item in ordered] == ["release", "paper"]


def test_cluster_downweights_biotech_against_compute_papers() -> None:
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    bio = _document("bio", "AlphaFold protein language genome model", now).model_copy(update={
        "content_type": "paper",
        "summary": "protein language alphafold genome method experiment architecture",
    })
    chip = _document("chip", "New GPU accelerator inference benchmark", now).model_copy(update={
        "content_type": "paper",
        "summary": "accelerator benchmark architecture method experiment",
    })
    topics = [
        {"id": "biotech", "name": "生物技术", "keywords": ["protein language", "alphafold", "genome"]},
        {"id": "compute_chips", "name": "芯片算力", "keywords": ["gpu", "accelerator", "benchmark"]},
    ]
    events = cluster_documents([bio, chip], topics, 72, 80)
    by_topic = {item.topic_id: item for item in events}
    assert "biotech" in by_topic and "compute_chips" in by_topic
    assert by_topic["compute_chips"].deterministic_score > by_topic["biotech"].deterministic_score


def test_cluster_excludes_obvious_nightly_release_noise() -> None:
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    noisy = _document("n", "trunk/68d20d4ee3956ceb: Disable CUDA arch", now).model_copy(
        update={"content_type": "github_release"}
    )
    topics = [{"id": "compute", "name": "芯片", "keywords": ["cuda"]}]
    assert cluster_documents([noisy], topics, 72, 80) == []


def test_selector_prioritizes_topic_coverage_before_repeats() -> None:
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)

    def paper(identifier: str, title: str) -> Document:
        return _document(identifier, title, now).model_copy(update={"content_type": "paper"})

    def pair(identifier: str, topic: str, doc: Document) -> tuple[Event, list[Document]]:
        return Event(
            id=identifier, title=doc.title, topic_id=topic, topic_name=topic,
            document_ids=[doc.id], first_seen=now, last_seen=now,
            source_quality=80, deterministic_score=80,
        ), [doc]

    bio1 = pair("b1", "biotech", paper("d1", "Genome paper one architecture"))
    bio2 = pair("b2", "biotech", paper("d2", "Genome paper two method"))
    chip = pair("c1", "compute", paper("d3", "GPU accelerator benchmark"))
    ordered = EventSelector.prioritize_for_research(
        [bio1, bio2, chip], preferred_general=0, preferred_hardcore=3,
        preferred_max_per_topic=1,
    )
    assert [item[0].id for item in ordered] == ["b1", "c1", "b2"]


def test_cluster_excludes_ciflow_release_even_when_title_is_a_real_bugfix() -> None:
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    noisy = _document("ci", "Fix dropout on complex MPS inputs", now).model_copy(update={
        "content_type": "github_release",
        "url": "https://github.com/pytorch/pytorch/releases/tag/ciflow%2Fmps%2F195373",
        "canonical_url": "https://github.com/pytorch/pytorch/releases/tag/ciflow%2Fmps%2F195373",
        "external_id": "ciflow/mps/195373",
        "summary": "cuda dropout architecture",
        "content": "cuda dropout architecture",
    })
    topics = [{"id": "compute", "name": "芯片", "keywords": ["cuda", "dropout"]}]
    assert cluster_documents([noisy], topics, 72, 80) == []


def test_full_text_failure_keeps_summary_and_marks_error(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _document("c", "Robot model", datetime.now(timezone.utc)).model_copy(
        update={"metadata": {"source_name": "Primary", "fetch_full_text": True}}
    )
    monkeypatch.setattr(
        "daily_intel.intelligence.extraction.http_get",
        lambda *a, **k: (_ for _ in ()).throw(TimeoutError("timeout")),
    )
    result = enrich_document(doc, 1, 1000)
    assert result.extraction_quality == "summary"
    assert "TimeoutError" in result.metadata["extraction_error"]
