from __future__ import annotations

from datetime import datetime, timedelta, timezone

from daily_intel.core.models import (
    Analysis, AnalysisQuality, AnalysisStatus, Document, Event, Evidence,
)
from daily_intel.github.pipeline import annotate_github_visuals
from daily_intel.github.trending import (
    append_catalog,
    merge_trending,
    parse_gitlab_projects,
    parse_huggingface_models,
    parse_trending_html,
)
from daily_intel.infrastructure.storage.sqlite import SQLiteIntelligenceRepository
from daily_intel.intelligence.selection import EventSelector


DAILY_HTML = """
<article class="Box-row">
  <h2><a href="/huggingface/transformers">huggingface / transformers</a></h2>
  <p>State-of-the-art Machine Learning for JAX, PyTorch and TensorFlow</p>
  <span itemprop="programmingLanguage">Python</span>
  <a href="/huggingface/transformers/stargazers">150,000</a>
  <span>1,234 stars today</span>
</article>
<article class="Box-row">
  <h2><a href="/vercel/next.js">vercel / next.js</a></h2>
  <p>The React Framework</p>
  <span itemprop="programmingLanguage">JavaScript</span>
  <span>800 stars today</span>
</article>
"""

WEEKLY_HTML = """
<article class="Box-row">
  <h2><a href="/huggingface/transformers">huggingface / transformers</a></h2>
  <p>State-of-the-art Machine Learning</p>
  <span>9,000 stars this week</span>
</article>
<article class="Box-row">
  <h2><a href="/openai/whisper">openai / whisper</a></h2>
  <p>Robust Speech Recognition</p>
  <span>4,200 stars this week</span>
</article>
"""


def test_parse_and_merge_hottest_and_fastest() -> None:
    daily = parse_trending_html(DAILY_HTML, "daily")
    weekly = parse_trending_html(WEEKLY_HTML, "weekly")
    assert [item["full_name"] for item in daily] == [
        "huggingface/transformers", "vercel/next.js",
    ]
    assert daily[0]["stars_today"] == 1234
    assert daily[0]["language"] == "Python"
    merged = merge_trending(daily, weekly, daily_limit=8, weekly_limit=8, publish_limit=10)
    names = [item["full_name"] for item in merged]
    assert names[0] == "huggingface/transformers"
    assert "今日最热" in merged[0]["reason"] and "本周增长最快" in merged[0]["reason"]
    assert "openai/whisper" in names
    assert "vercel/next.js" in names
    annotated, chart = annotate_github_visuals(merged)
    assert chart["count"] == 3
    assert annotated[0]["today_width"] == 100
    assert annotated[0]["week_width"] == 100
    assert "█" in annotated[0]["today_spark"]
    hf = parse_huggingface_models([
        {"modelId": "meta-llama/Llama-3.1-8B", "likes": 9000, "pipeline_tag": "text-generation"},
        {"id": "broken", "likes": 1},
    ], limit=4)
    gitlab = parse_gitlab_projects([
        {
            "path_with_namespace": "gitlab-org/gitlab",
            "web_url": "https://gitlab.com/gitlab-org/gitlab",
            "description": "GitLab CE",
            "star_count": 5000,
        }
    ], limit=3)
    catalog = append_catalog(merged, hf + gitlab)
    catalog_names = [item["full_name"] for item in catalog]
    assert "meta-llama/Llama-3.1-8B" in catalog_names
    assert "gitlab-org/gitlab" in catalog_names
    by_name = {item["full_name"]: item for item in catalog}
    assert by_name["meta-llama/Llama-3.1-8B"]["origin"] == "huggingface"
    assert by_name["gitlab-org/gitlab"]["origin"] == "gitlab"


def test_repeat_publication_lowers_next_day_rank(tmp_path) -> None:
    now = datetime(2026, 8, 28, 8, tzinfo=timezone.utc)
    repository = SQLiteIntelligenceRepository(tmp_path / "intel.db")
    old_doc = Document(
        id="doc-old", source_id="github", source_name="GitHub", external_id="t1",
        title="Transformers v5.16.0",
        url="https://github.com/huggingface/transformers/releases/tag/v5.16.0",
        canonical_url="https://github.com/huggingface/transformers/releases/tag/v5.16.0",
        published_at=now - timedelta(hours=30), fetched_at=now,
        summary="release", content="release", content_hash="a" * 64, source_tier=1,
    )
    new_doc = Document(
        id="doc-new", source_id="arxiv", source_name="arXiv", external_id="p1",
        title="New robot paper", url="https://arxiv.org/abs/2608.99999",
        canonical_url="https://arxiv.org/abs/2608.99999",
        published_at=now - timedelta(hours=2), fetched_at=now,
        summary="robot", content="robot", content_hash="b" * 64, source_tier=1,
    )
    old_event = Event(
        id="old-event", title=old_doc.title, topic_id="models", topic_name="模型",
        document_ids=["doc-old"], first_seen=old_doc.published_at, last_seen=old_doc.published_at,
        source_quality=80, deterministic_score=90,
    )
    new_event = Event(
        id="new-event", title=new_doc.title, topic_id="robot", topic_name="机器人",
        document_ids=["doc-new"], first_seen=new_doc.published_at, last_seen=new_doc.published_at,
        source_quality=80, deterministic_score=80,
    )
    repository.upsert_document(old_doc)
    repository.upsert_document(new_doc)
    repository.upsert_event(old_event)
    repository.upsert_event(new_event)
    repository.save_analysis(
        Analysis(
            event_id="old-event", status=AnalysisStatus.DEEP, headline=old_doc.title,
            plain_takeaway="昨天已经写过这条发行说明。",
            key_facts=["已发布"], confidence=.7,
            evidence=[Evidence(
                document_id="doc-old", url=old_doc.url,
                quote="Transformers v5.16.0 release notes body.", locator="正文",
            )],
            quality=AnalysisQuality(policy_version="evidence-gate-v2", passed=True, score=90),
            model="fixture", prompt_version="tech-intel-v3",
            created_at=now - timedelta(hours=22),
        ),
        "scope",
    )
    selector = EventSelector(
        {"intelligence": {
            "selection_deterministic_weight": .65, "selection_model_weight": .35,
            "selection_model_reject_floor": 55, "selection_repeat_penalty": .4,
            "selection_repeat_hours": 36,
        }},
        repository,
        stages=None,  # type: ignore[arg-type]
    )
    ordered = selector.order_with_repeat(
        [(old_event, [old_doc]), (new_event, [new_doc])], now,
    )
    assert ordered[0][0].id == "new-event"
    assert selector._repeat_factor(old_event, [old_doc], selector._recent_analyses(now)) == .4
