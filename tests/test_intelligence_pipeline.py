from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from daily_intel.core.models import (
    AnalysisDraft, Document, Event, Evidence, ScoutBatch, ScoutItem, VerificationResult,
)
from daily_intel.core.ports import LLMClient, LLMResult
from daily_intel.infrastructure.storage.sqlite import SQLiteIntelligenceRepository
from daily_intel.intelligence.pipeline import IntelligencePipeline


NOW = datetime(2026, 8, 24, 10, tzinfo=timezone.utc)
CONTENT = "First supported technical fact in the source. Second supported benchmark result in the source."


def settings() -> dict:
    return {
        "intelligence": {
            "first_run_lookback_hours": 48, "resume_overlap_hours": 6,
            "cluster_window_hours": 72, "max_items_per_source": 30,
            "scout_batch_size": 30, "intensive_reading_events": 2,
            "offline_analysis_events": 50,
            "preferred_general_events": 2, "preferred_hardcore_events": 3,
            "preferred_max_per_topic": 2,
            "full_text_max_chars": 50000,
            "title_similarity_threshold": 88, "source_fetch_timeout_seconds": 1,
        },
        "llm": {
            "prompt_version": "test-v1",
            "scout": {"model": "scout"}, "analyst": {"model": "analyst"},
            "verifier": {"model": "verifier"},
        },
        "topics": [{"id": "compute", "name": "芯片算力", "keywords": ["accelerator", "benchmark"]}],
        "sources": {},
    }


def document() -> Document:
    return Document(
        id="doc-1", source_id="primary", source_name="Primary", external_id="1",
        title="New AI accelerator benchmark", url="https://example.com/doc",
        canonical_url="https://example.com/doc", published_at=NOW, fetched_at=NOW,
        summary=CONTENT, content=CONTENT, content_hash="d" * 64, source_tier=1,
        metadata={"source_name": "Primary", "fetch_full_text": False},
    )


class FakeLLM(LLMClient):
    def __init__(self, fail_analyst: bool = False) -> None:
        self.calls: list[str] = []
        self.scout_payloads: list[dict] = []
        self.fail_analyst = fail_analyst

    @property
    def available(self) -> bool:
        return True

    def generate(self, stage, system, user, schema):
        self.calls.append(stage)
        if stage == "scout":
            payload = json.loads(user)
            self.scout_payloads.append(payload)
            value = ScoutBatch(items=[
                ScoutItem(
                    event_id=item["event_id"], relevant=True, topic_id="compute",
                    relevance=95, novelty=90, technical_depth=90,
                    industry_impact=80, reason="material",
                )
                for item in payload["events"]
            ])
        elif stage == "analyst":
            if self.fail_analyst:
                raise ValueError("invalid model JSON")
            value = AnalysisDraft(
                headline="加速器基准更新",
                plain_takeaway="新加速器公布了可核对的基准结果，实际效果还要等独立复现。",
                key_facts=[
                    "已发布新的基准结果",
                    "来源披露了技术实现路径",
                    "结果仍需要独立复现",
                ],
                technical_mechanism="通过新架构提升推理吞吐。", novelty="新的工程实现。",
                maturity="原型验证。", outlook_6_24m="可能影响部署成本。",
                risks=["基准尚未独立复现", "实际部署成本仍不确定"],
                counterpoints=["实际负载可能不同"], confidence=.8,
                evidence=[
                    Evidence(document_id="doc-1", url="https://example.com/doc", quote="First supported technical fact in the source.", locator="正文1"),
                    Evidence(document_id="doc-1", url="https://example.com/doc", quote="Second supported benchmark result in the source.", locator="正文2"),
                ],
            )
        else:
            value = VerificationResult(
                supported_evidence_indexes=[0, 1], unsupported_claims=[],
                confidence_adjustment=0, verdict="pass", notes="supported",
            )
        return LLMResult(value, stage, 10, 5)


class UnavailableLLM(FakeLLM):
    @property
    def available(self) -> bool:
        return False


def _pipeline(tmp_path, llm: LLMClient, monkeypatch) -> IntelligencePipeline:
    repository = SQLiteIntelligenceRepository(tmp_path / "intel.db")
    pipeline = IntelligencePipeline(settings(), repository, llm)
    monkeypatch.setattr(
        pipeline.collector, "collect_sources",
        lambda now: ([document()], [{"name": "primary", "source": "Primary", "fetched_at": now.isoformat(), "stale": False, "count": 1, "error": ""}]),
    )
    return pipeline


def test_ai_pipeline_deep_analysis_and_same_day_reuse(tmp_path, monkeypatch) -> None:
    llm = FakeLLM()
    pipeline = _pipeline(tmp_path, llm, monkeypatch)
    first = pipeline.run(NOW, pd.DataFrame())
    second = pipeline.run(NOW, pd.DataFrame())
    assert first.analyses[0].status.value == "deep"
    assert len(first.analyses[0].evidence) == 2
    assert second.analyses[0].event_id == first.analyses[0].event_id
    assert llm.calls == ["scout", "analyst", "verifier"]
    assert first.analysis_cache_misses == 1
    assert second.analysis_cache_hits == 1
    assert first.model_runtime["models"]["analyst"] == "analyst"
    assert second.model_runtime["models"] == {}
    assert second.model_runtime["analysis_models"] == ["analyst"]

    alternate = pipeline.run(NOW, pd.DataFrame(), experiment_id="alternate-model")
    assert alternate.cache_scope != first.cache_scope
    assert llm.calls == [
        "scout", "analyst", "verifier",
        "scout", "analyst", "verifier",
    ]
    forced = pipeline.run(
        NOW, pd.DataFrame(),
        experiment_id="alternate-model", force_analysis=True,
    )
    assert forced.analysis_cache_misses == 1
    assert llm.calls[-2:] == ["analyst", "verifier"]


def test_invalid_ai_output_is_retried_then_skipped(tmp_path, monkeypatch) -> None:
    llm = FakeLLM(fail_analyst=True)
    pipeline = _pipeline(tmp_path, llm, monkeypatch)
    result = pipeline.run(NOW, pd.DataFrame())
    assert result.analyses == []
    assert llm.calls.count("analyst") == 2
    assert any("invalid model JSON" in item for item in result.errors)


def test_no_ai_publishes_explicit_lead_and_offline_reuses_it(tmp_path, monkeypatch) -> None:
    pipeline = _pipeline(tmp_path, UnavailableLLM(), monkeypatch)
    live = pipeline.run(NOW, pd.DataFrame())
    offline = pipeline.run(NOW, pd.DataFrame(), offline=True)
    assert live.analyses[0].status.value == "lead"
    assert live.analyses[0].model == "none"
    assert offline.ai_status == "cached"
    assert offline.analyses[0].event_id == live.analyses[0].event_id


def test_soft_lane_and_topic_preferences_backfill_open_slots(tmp_path, monkeypatch) -> None:
    def paper(identifier: str, title: str, summary: str) -> Document:
        return Document(
            id=identifier, source_id="primary", source_name="Primary",
            external_id=identifier, title=title,
            url=f"https://example.com/{identifier}",
            canonical_url=f"https://example.com/{identifier}",
            published_at=NOW, fetched_at=NOW, summary=summary, content=summary,
            content_hash=identifier * 8, source_tier=1, content_type="paper",
            metadata={"source_name": "Primary", "lane": "hardcore"},
        )

    docs = [
        paper("b1", "CARP pangenome genome reconstruction", "genome method experiment"),
        paper("b2", "Perturb-seq hematopoietic genome screen", "genome dataset evaluation"),
        paper("b3", "Drosophila mutation genome fertility", "genome architecture method"),
        paper("c1", "New GPU accelerator inference benchmark", "accelerator benchmark architecture"),
    ]
    cfg = settings()
    cfg["intelligence"].update({
        "preferred_general_events": 0, "preferred_hardcore_events": 4,
        "intensive_reading_events": 2, "preferred_max_per_topic": 2,
    })
    cfg["topics"] = [
        {"id": "biotech", "name": "生物技术", "keywords": ["genome"]},
        {"id": "compute_chips", "name": "芯片算力", "keywords": ["accelerator", "gpu"]},
    ]
    repository = SQLiteIntelligenceRepository(tmp_path / "intel.db")
    pipeline = IntelligencePipeline(cfg, repository, UnavailableLLM())
    monkeypatch.setattr(
        pipeline.collector, "collect_sources",
        lambda now: (docs, [{"name": "primary", "source": "Primary", "fetched_at": now.isoformat(), "stale": False, "count": len(docs), "error": ""}]),
    )
    result = pipeline.run(NOW, pd.DataFrame())
    headlines = [item.headline for item in result.analyses]
    assert len(headlines) == 4
    assert "New GPU accelerator inference benchmark" in headlines
    assert sum("genome" in title.lower() for title in headlines) == 3
    assert result.processing_funnel["research_target_events"] == 4
    assert result.processing_funnel["research_attempted_events"] == 4
    assert result.processing_funnel["intensive_reading_events"] == 2
    assert result.processing_funnel["extensive_reading_events"] == 2
    published = [
        item for item in result.processing_trace if item.get("status") == "published"
    ]
    assert {item["publication_tier"] for item in published} == {
        "intensive", "extensive",
    }


def test_akshare_radar_news_joins_the_same_scout_and_research_path(
    tmp_path, monkeypatch,
) -> None:
    llm = FakeLLM()
    pipeline = _pipeline(tmp_path, llm, monkeypatch)
    radar = pd.DataFrame([{
        "title": "半导体设备出口政策发生变化",
        "summary": CONTENT,
        "published_at": NOW.isoformat(),
        "url": "https://example.com/market-radar",
    }])

    result = pipeline.run(NOW, radar)

    scout_titles = {
        event["title"]
        for payload in llm.scout_payloads
        for event in payload["events"]
    }
    assert "半导体设备出口政策发生变化" in scout_titles
    assert len(result.analyses) == 2
    assert llm.calls.count("analyst") == 2
    assert llm.calls.count("verifier") == 2


def test_unclassified_official_release_reaches_scout_and_trace(tmp_path, monkeypatch) -> None:
    cfg = settings()
    cfg["topics"] = [{"id": "compute", "name": "芯片算力", "keywords": ["never-match"]}]
    repository = SQLiteIntelligenceRepository(tmp_path / "intel.db")
    llm = FakeLLM()
    pipeline = IntelligencePipeline(cfg, repository, llm)
    fable = document().model_copy(update={
        "title": "Introducing Claude Fable 5.1 and Claude Mythos 5.1",
        "summary": "First supported technical fact in the source. Second supported benchmark result in the source.",
    })
    monkeypatch.setattr(
        pipeline.collector, "collect_sources",
        lambda now: ([fable], [{
            "name": "anthropic_official", "source": "Anthropic", "fetched_at": now.isoformat(),
            "stale": False, "count": 1, "error": "",
        }]),
    )
    result = pipeline.run(NOW, pd.DataFrame())
    assert result.analyses[0].headline == "加速器基准更新"
    assert result.processing_funnel["unclassified_documents"] == 1
    trace = next(item for item in result.processing_trace if item.get("event_id"))
    assert trace["topic_id"] == "compute"
    assert trace["official_release_priority"] is True
    assert trace["status"] == "published"


def test_require_ai_rejects_missing_key_and_offline(tmp_path, monkeypatch) -> None:
    pipeline = _pipeline(tmp_path, UnavailableLLM(), monkeypatch)
    with pytest.raises(RuntimeError, match="require-ai"):
        pipeline.run(NOW, pd.DataFrame(), require_ai=True)
    with pytest.raises(RuntimeError, match="require-ai"):
        pipeline.run(NOW, pd.DataFrame(), offline=True, require_ai=True)


def test_scout_user_sends_body_excerpt_not_a_short_summary_only() -> None:
    from daily_intel.intelligence.prompts import scout_user

    body = ("mechanism and benchmark result. " * 80).strip()
    doc = document().model_copy(update={"summary": "short rss blurb", "content": body})
    event = Event(
        id="e1", title=doc.title, topic_id="compute", topic_name="芯片",
        document_ids=[doc.id], first_seen=NOW, last_seen=NOW,
        source_quality=80, deterministic_score=80,
    )
    payload = json.loads(scout_user([(event, [doc])], [{"id": "compute", "name": "芯片"}], doc_chars=4000))
    excerpt = payload["events"][0]["sources"][0]["excerpt"]
    assert "short rss blurb" not in excerpt
    assert excerpt.startswith("mechanism and benchmark result.")
    assert len(excerpt) > 1500
