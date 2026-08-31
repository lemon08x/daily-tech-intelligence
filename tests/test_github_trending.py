from __future__ import annotations

from datetime import datetime, timedelta, timezone

from daily_intel.core.models import (
    Analysis, AnalysisQuality, AnalysisStatus, Document, Event, Evidence,
)
from daily_intel.core.models import GitProjectBrief, GitBriefingBatch
from daily_intel.github.pipeline import annotate_github_visuals, apply_git_brief
from daily_intel.github.trending import (
    fetch_github_project_context,
    fetch_github_readme,
    merge_trending,
    parse_trending_html,
)
from daily_intel.intelligence.prompts import git_brief_user
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
    assert daily[0]["stars_total"] == 150000
    assert daily[0]["language"] == "Python"
    merged = merge_trending(daily, weekly, daily_limit=8, weekly_limit=8, publish_limit=10)
    names = [item["full_name"] for item in merged]
    assert names[0] == "huggingface/transformers"
    assert "今日最热" in merged[0]["reason"] and "本周增长最快" in merged[0]["reason"]
    assert "openai/whisper" in names
    assert "vercel/next.js" in names
    annotated = annotate_github_visuals(merged)
    assert len(annotated) == 3
    assert annotated[0]["today_width"] == 100
    assert annotated[0]["week_width"] == 100
    assert annotated[0]["stars_total_label"] == "15万"
    assert annotated[0]["plain"].startswith("State-of-the-art")
    assert "这是一个" not in annotated[0]["plain"]
    apply_git_brief(
        GitBriefingBatch(items=[GitProjectBrief(
            full_name="huggingface/transformers",
            kicker="模型",
            function="给开发者提供现成的模型接口，用来加载和运行各种大模型。",
        )]),
        annotated,
    )
    assert annotated[0]["kicker"] == "模型"
    assert "加载和运行" in annotated[0]["plain"]


class _FakeResponse:
    def __init__(self, text: str = "", payload: object | None = None, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self) -> object:
        return self._payload


def test_fetch_github_readme_and_manifest_when_readme_is_thin(monkeypatch) -> None:
    def fake_get(url, timeout=0, headers=None, **kwargs):
        if url.endswith("/readme"):
            return _FakeResponse("# tiny\n")
        if url.endswith("/contents/"):
            return _FakeResponse(payload=[
                {"name": "README.md"},
                {"name": "package.json"},
                {"name": "src"},
            ])
        if url.endswith("/contents/package.json"):
            return _FakeResponse(payload={
                "encoding": "utf-8",
                "content": '{"name":"demo","description":"Load models for apps"}',
            })
        raise AssertionError(url)

    monkeypatch.setattr("daily_intel.github.trending.http_get", fake_get)
    context = fetch_github_project_context("acme/demo")
    assert context["readme"].startswith("# tiny")
    assert "package.json" in context["root_files"]
    assert "Load models for apps" in context["manifest"]

    long_readme = fetch_github_readme("acme/demo")
    assert long_readme.startswith("# tiny")
    payload = git_brief_user([{
        "full_name": "acme/demo",
        "description": "demo",
        "language": "Python",
        "readme": "# Load and run language models\n",
        "root_files": "README.md src",
        "manifest": "",
    }])
    assert "Load and run language models" in payload


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
