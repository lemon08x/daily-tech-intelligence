from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from daily_intel.app.cli import build_parser
from daily_intel.app.orchestrator import run_application
from daily_intel.core.models import Analysis, AnalysisStatus, Evidence
from daily_intel.core.settings import load_settings, resolve_path
from daily_intel.infrastructure.storage.sqlite import SQLiteIntelligenceRepository
from daily_intel.intelligence.pipeline import IntelligenceRunResult
from daily_intel.market.pipeline import MarketRunResult
from daily_intel.publication.reporting import publish


def test_new_and_legacy_config_resolve_same_project_paths() -> None:
    root = Path(__file__).resolve().parents[1]
    new = load_settings(root / "config" / "settings.yaml")
    legacy = load_settings(root / "config.yaml")
    assert resolve_path(new, "cache_dir") == (root / "data" / "cache").resolve()
    assert resolve_path(legacy, "cache_dir") == (root / "data" / "cache").resolve()
    assert new["market"]["factor_weights"] == legacy["market"]["factor_weights"]


def test_cli_supports_new_flags_and_legacy_command_name() -> None:
    args = build_parser().parse_args([
        "run", "--offline", "--no-ai",
        "--experiment-id", "qwen3.8-27b", "--force-analysis",
    ])
    assert args.offline and args.no_ai
    assert args.experiment_id == "qwen3.8-27b"
    assert args.force_analysis


def test_publish_writes_unified_outputs(tmp_path) -> None:
    now = datetime(2026, 8, 24, 10, tzinfo=timezone.utc)
    analysis = Analysis(
        event_id="event-1", status=AnalysisStatus.LEAD, headline="技术线索",
        key_facts=["AI未启用，仅展示来源线索"], risks=["尚未深研"], confidence=.3,
        evidence=[Evidence(
            document_id="doc-1", url="https://example.com/source",
            quote="This is a sufficiently long source quotation.", locator="来源摘要",
        )], model="none", prompt_version="test-v1", created_at=now,
    )
    breadth = {
        "mood": "分化", "advancing": 1, "declining": 1, "flat": 0,
        "advance_ratio": .5, "median_change": 0, "amount_cny": 100,
        "limit_up_like": 0, "limit_down_like": 0, "total": 2,
    }
    context = {
        "title": "测试日报", "report_date": "2026-08-24", "market_date": "2026-08-24",
        "generated_at": "2026-08-24 18:10 CST", "is_trading_day": True,
        "analyses": [analysis], "ai_status": "disabled", "ai_status_label": "AI未启用",
        "usage": {"calls": 0, "input_tokens": 0, "output_tokens": 0},
        "prompt_version": "test-v1", "pipeline_errors": [], "breadth": breadth,
        "eligible_count": 0, "candidate_records": [], "hot_industry_records": [],
        "weak_industry_records": [], "index_records": [], "news_records": [],
        "market_source_status": [], "intelligence_source_status": [],
        "weights": {"momentum": .3, "value": .2, "liquidity": .15, "activity": .15, "daily_strength": .1, "size": .1},
    }
    metadata = {
        "run_name": "100000-test",
        "experiment_id": "test",
        "ai": {"status": "disabled"},
    }
    outputs = publish(
        context, [analysis], pd.DataFrame(), pd.DataFrame(), metadata,
        tmp_path, now,
    )
    assert set(outputs) == {"html", "markdown", "csv", "snapshot", "intelligence", "metadata"}
    assert "科技前沿深研" in outputs["html"].read_text(encoding="utf-8")
    payload = json.loads(outputs["intelligence"].read_text(encoding="utf-8"))
    assert payload["analyses"][0]["status"] == "lead"
    assert outputs["html"].parent == tmp_path / "2026-08-24" / "runs" / "100000-test"
    assert (tmp_path / "2026-08-24" / "daily_digest.html").exists()
    latest = json.loads(
        (tmp_path / "2026-08-24" / "latest_run.json").read_text(encoding="utf-8")
    )
    assert latest["run_name"] == "100000-test"


def test_publish_preserves_multiple_same_day_runs(tmp_path) -> None:
    now = datetime(2026, 8, 24, 10, tzinfo=timezone.utc)
    breadth = {
        "mood": "平稳", "advancing": 0, "declining": 0, "flat": 0,
        "advance_ratio": 0, "median_change": 0, "amount_cny": 0,
        "limit_up_like": 0, "limit_down_like": 0, "total": 0,
    }
    context = {
        "title": "测试", "report_date": "2026-08-24", "market_date": "2026-08-24",
        "generated_at": "2026-08-24 10:00 CST", "is_trading_day": True,
        "analyses": [], "ai_status": "disabled", "ai_status_label": "AI未启用",
        "usage": {"calls": 0, "input_tokens": 0, "output_tokens": 0},
        "prompt_version": "test", "pipeline_errors": [], "breadth": breadth,
        "eligible_count": 0, "candidate_records": [], "hot_industry_records": [],
        "weak_industry_records": [], "index_records": [], "news_records": [],
        "market_source_status": [], "intelligence_source_status": [],
        "weights": {},
    }
    first = publish(
        {**context, "run_name": "run-a", "experiment_id": "model-a"},
        [], pd.DataFrame(), pd.DataFrame(),
        {"run_name": "run-a", "experiment_id": "model-a"}, tmp_path, now,
    )
    second = publish(
        {**context, "run_name": "run-b", "experiment_id": "model-b"},
        [], pd.DataFrame(), pd.DataFrame(),
        {"run_name": "run-b", "experiment_id": "model-b"}, tmp_path, now,
    )
    assert first["html"].exists() and second["html"].exists()
    assert first["html"] != second["html"]
    latest = json.loads(
        (tmp_path / "2026-08-24" / "latest_run.json").read_text(encoding="utf-8")
    )
    assert latest["run_name"] == "run-b"


def test_orchestrator_uses_injected_workflows_publisher_and_actual_model_metadata(
    tmp_path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    settings = load_settings(root / "config" / "settings.yaml")
    now = datetime(2026, 8, 25, 10, tzinfo=timezone.utc)
    market_result = MarketRunResult(
        snapshot=pd.DataFrame(),
        candidates=pd.DataFrame(),
        radar_news=pd.DataFrame(),
        context={
            "market_date": "2026-08-25",
            "is_trading_day": True,
            "breadth": {},
            "eligible_count": 0,
            "candidate_records": [],
            "hot_industry_records": [],
            "weak_industry_records": [],
            "index_records": [],
            "news_records": [],
            "market_source_status": [],
            "weights": settings["market"]["factor_weights"],
        },
        metadata={"market_date": "2026-08-25"},
    )
    intelligence_result = IntelligenceRunResult(
        analyses=[],
        source_status=[],
        ai_status="enabled",
        usage={
            "calls": 1,
            "input_tokens": 100,
            "output_tokens": 50,
            "estimated": True,
        },
        model_runtime={
            "provider": "qwen-code-agent",
            "models": {"analyst": "qwen-code-agent"},
            "usage_reporting": "estimated",
        },
        quality_summary={
            "policy_version": "evidence-gate-v1",
            "average_score": 0,
            "passed": 0,
            "downgraded": 0,
            "issue_counts": {},
        },
    )

    class MarketWorkflow:
        def run(self):
            return market_result

    class IntelligenceWorkflow:
        def run(self, *args, **kwargs):
            return intelligence_result

    class CapturingPublisher:
        metadata = None

        def publish(self, context, analyses, snapshot, candidates, metadata, output_dir, now):
            self.metadata = metadata
            return {"custom": tmp_path / "custom-output"}

    publisher = CapturingPublisher()
    outputs = run_application(
        settings,
        now=now,
        repository=SQLiteIntelligenceRepository(tmp_path / "intelligence.db"),
        market_workflow=MarketWorkflow(),
        intelligence_workflow=IntelligenceWorkflow(),
        publisher=publisher,
    )
    assert outputs == {"custom": tmp_path / "custom-output"}
    assert publisher.metadata["ai"]["provider"] == "qwen-code-agent"
    assert publisher.metadata["ai"]["usage_reporting"] == "estimated"
    assert publisher.metadata["intelligence"]["quality"]["policy_version"] == "evidence-gate-v1"
