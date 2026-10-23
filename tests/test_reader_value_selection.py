from pathlib import Path

import pandas as pd
import pytest
import yaml

from daily_intel.core.settings import load_settings
from daily_intel.intelligence.modeling import ModelStageRunner
from daily_intel.intelligence.quality import QualityPolicy
from test_intelligence_pipeline import BroadReadingLLM, NOW, _broad_pipeline


@pytest.mark.parametrize("broad_enabled", [True, False])
def test_low_value_events_cannot_be_rescued_by_rule_score_or_backfill(
    tmp_path, monkeypatch, broad_enabled,
):
    pipeline, llm = _broad_pipeline(tmp_path, monkeypatch)
    pipeline.selector.broad_reading_enabled = broad_enabled
    generate = llm.generate

    def assess_value(stage, system, user, schema):
        result = generate(stage, system, user, schema)
        if stage.startswith("scout"):
            for item in result.value.items:
                item.relevance = 90 if item.event_id in {"event-0", "event-3"} else 35
                item.relevant = item.event_id != "event-0"
                item.novelty = item.technical_depth = item.industry_impact = 100
                item.reason = "有具体用途" if item.event_id == "event-3" else "只有细分指标增量"
        return result

    monkeypatch.setattr(llm, "generate", assess_value)
    first = pipeline.run(NOW, pd.DataFrame(), require_ai=True)
    assert [a.event_id for a in first.analyses] == ["event-3"]
    assert llm.calls.count("analyst") == llm.calls.count("verifier") == 1
    rejected = [t for t in first.processing_trace if t["status"] == "scout_rejected"]
    assert len(rejected) == 7
    assert all("阅读价值不足" in t["reason"] for t in rejected)
    assert first.processing_funnel["scout_rejected_events"] == 7
    assert "event-0" not in pipeline.selector.priority_event_ids
    calls = len(llm.calls)
    cached = pipeline.run(NOW, pd.DataFrame(), require_ai=True)
    assert [a.event_id for a in cached.analyses] == ["event-3"]
    assert len(llm.calls) == calls


def test_rerank_can_reject_without_filling_shortlist_or_losing_trace(tmp_path, monkeypatch):
    pipeline, llm = _broad_pipeline(tmp_path, monkeypatch)
    generate = llm.generate

    def reassess_value(stage, system, user, schema):
        result = generate(stage, system, user, schema)
        if stage == "scout_rerank":
            for item in result.value.items:
                item.relevance = 90 if item.event_id == "event-4" else 45
                item.reason = "更新对实际瓶颈的认识" if item.event_id == "event-4" else "意义不清"
        return result

    monkeypatch.setattr(llm, "generate", reassess_value)
    first = pipeline.run(NOW, pd.DataFrame(), require_ai=True)
    assert [a.event_id for a in first.analyses] == ["event-4"]
    assert first.processing_funnel["broad_reading_shortlist_events"] == 6
    assert first.processing_funnel["rerank_rejected_events"] == 5
    assert first.processing_funnel["scout_rejected_events"] == 5
    assert llm.calls.count("analyst") == 1
    trace = {t["event_id"]: t for t in first.processing_trace}
    assert trace["event-0"]["status"] == "scout_rejected"
    assert trace["event-0"]["reader_relevance"] == 45
    assert trace["event-6"]["status"] == "broad_not_shortlisted"
    calls = len(llm.calls)
    cached = pipeline.run(NOW, pd.DataFrame(), require_ai=True)
    assert [a.event_id for a in cached.analyses] == ["event-4"]
    assert cached.processing_funnel["rerank_rejected_events"] == 5
    assert len(llm.calls) == calls


def test_empty_editorial_selection_stays_empty_and_cached(tmp_path, monkeypatch):
    pipeline, llm = _broad_pipeline(tmp_path, monkeypatch)
    generate = llm.generate

    def no_value(stage, system, user, schema):
        result = generate(stage, system, user, schema)
        if stage.startswith("scout"):
            for item in result.value.items:
                item.relevance = 20
        return result

    monkeypatch.setattr(llm, "generate", no_value)
    first = pipeline.run(NOW, pd.DataFrame(), require_ai=True)
    assert first.analyses == []
    assert llm.calls == ["scout_broad"]
    second = pipeline.run(NOW, pd.DataFrame(), require_ai=True)
    assert second.analyses == []
    assert llm.calls == ["scout_broad"]


def test_reader_benefit_outranks_technical_complexity(tmp_path, monkeypatch):
    pipeline, llm = _broad_pipeline(tmp_path, monkeypatch, count=2)
    pipeline.selector.shortlist_events = 1
    generate = llm.generate

    def compare_value(stage, system, user, schema):
        result = generate(stage, system, user, schema)
        if stage.startswith("scout"):
            for item in result.value.items:
                if item.event_id == "event-0":
                    item.relevance, item.novelty, item.technical_depth, item.industry_impact = 60, 100, 100, 40
                else:
                    item.relevance, item.novelty, item.technical_depth, item.industry_impact = 95, 40, 30, 85
        return result

    monkeypatch.setattr(llm, "generate", compare_value)
    result = pipeline.run(NOW, pd.DataFrame(), require_ai=True)
    assert [a.event_id for a in result.analyses] == ["event-1"]


def test_value_threshold_change_invalidates_selection_cache(tmp_path, monkeypatch):
    pipeline, llm = _broad_pipeline(tmp_path, monkeypatch)
    first = pipeline.run(NOW, pd.DataFrame(), require_ai=True)
    assert len(first.analyses) == 6
    pipeline.selector.min_reader_relevance = 90
    second = pipeline.run(NOW, pd.DataFrame(), require_ai=True)
    assert [a.event_id for a in second.analyses] == ["event-0"]
    assert llm.calls.count("scout_broad") == 2


@pytest.mark.parametrize("filename", [
    "settings.yaml", "settings.daily-gpt.yaml", "settings.daily-qwen.yaml",
    "settings.deepseek.yaml", "settings.qwen.yaml",
])
def test_shipped_configs_isolate_old_editorial_analysis_cache(filename):
    settings = load_settings(Path(__file__).resolve().parents[1] / "config" / filename)
    llm = BroadReadingLLM("fixture")
    policy = QualityPolicy.from_settings(settings)
    current = ModelStageRunner(settings, None, llm, policy)
    old_scope = current.cache_scope("editorial-check")
    assert current.prompt_version == "tech-intel-v13-reader-value"
    assert settings["broad_reading"]["prompt_version"] == "broad-reading-v8-reader-value"
    settings["llm"]["prompt_version"] = "tech-intel-v12-informative"
    previous = ModelStageRunner(settings, None, llm, policy)
    assert previous.cache_scope("editorial-check") != old_scope


def test_invalid_reader_value_threshold_is_rejected(tmp_path):
    # Loading a real config exercises default merging as well as validation.
    root = Path(__file__).resolve().parents[1] / "config"
    settings = yaml.safe_load((root / "settings.yaml").read_text(encoding="utf-8"))
    settings["intelligence"]["selection_min_reader_relevance"] = 101
    for filename in ("topics.yaml", "sources.yaml"):
        (tmp_path / filename).write_bytes((root / filename).read_bytes())
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(settings), encoding="utf-8")
    with pytest.raises(ValueError, match="selection_min_reader_relevance"):
        load_settings(path)
