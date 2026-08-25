from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from daily_intel.app.cli import build_parser
from daily_intel.core.models import Analysis, AnalysisStatus, Evidence
from daily_intel.core.settings import load_settings, resolve_path
from daily_intel.publication.reporting import publish


def test_new_and_legacy_config_resolve_same_project_paths() -> None:
    root = Path(__file__).resolve().parents[1]
    new = load_settings(root / "config" / "settings.yaml")
    legacy = load_settings(root / "config.yaml")
    assert resolve_path(new, "cache_dir") == (root / "data" / "cache").resolve()
    assert resolve_path(legacy, "cache_dir") == (root / "data" / "cache").resolve()
    assert new["market"]["factor_weights"] == legacy["market"]["factor_weights"]


def test_cli_supports_new_flags_and_legacy_command_name() -> None:
    args = build_parser().parse_args(["run", "--offline", "--no-ai"])
    assert args.offline and args.no_ai


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
    outputs = publish(
        context, [analysis], pd.DataFrame(), pd.DataFrame(), {"ai": {"status": "disabled"}},
        tmp_path, now,
    )
    assert set(outputs) == {"html", "markdown", "csv", "snapshot", "intelligence", "metadata"}
    assert "科技前沿深研" in outputs["html"].read_text(encoding="utf-8")
    payload = json.loads(outputs["intelligence"].read_text(encoding="utf-8"))
    assert payload["analyses"][0]["status"] == "lead"
