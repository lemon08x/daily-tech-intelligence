from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


WEIGHT_KEYS = {"momentum", "value", "liquidity", "activity", "daily_strength", "size"}
QUALITY_DEFAULTS = {
    "policy_version": "evidence-gate-v1",
    "min_key_facts": 3,
    "max_key_facts": 6,
    "min_supported_evidence": 2,
    "max_supported_evidence": 6,
    "min_primary_sources": 1,
    "min_risks": 2,
    "max_risks": 5,
    "min_counterpoints": 1,
    "max_counterpoints": 4,
    "max_industry_impacts": 4,
    "downgrade_on_unsupported_claims": True,
    "max_single_source_confidence": .85,
    "max_deep_confidence": .90,
    "lead_confidence_cap": .49,
    "section_max_chars": 900,
    "list_item_max_chars": 420,
}
INTELLIGENCE_DEFAULTS = {
    "selection_deterministic_weight": .65,
    "selection_model_weight": .35,
    "selection_model_reject_floor": 55,
}


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_settings(path: Path) -> dict[str, Any]:
    path = path.resolve()
    raw = _read_yaml(path)
    if "paths" not in raw and "screening" in raw:
        raw = _upgrade_legacy(raw, path.parent)
        config_dir = path.parent / "config"
    else:
        config_dir = path.parent
    for key, value in INTELLIGENCE_DEFAULTS.items():
        raw["intelligence"].setdefault(key, value)
    quality = raw.setdefault("quality", {})
    for key, value in QUALITY_DEFAULTS.items():
        quality.setdefault(key, value)
    raw["topics"] = _read_yaml(config_dir / "topics.yaml").get("topics", [])
    raw["sources"] = _read_yaml(config_dir / "sources.yaml")
    raw["_config_path"] = path
    raw["_config_dir"] = config_dir
    raw["_project_root"] = config_dir.parent if config_dir.name == "config" else config_dir
    _validate(raw)
    return raw


def _upgrade_legacy(raw: dict[str, Any], base: Path) -> dict[str, Any]:
    return {
        "app": raw["app"],
        "paths": {
            "cache_dir": str(base / raw["data"]["cache_dir"]),
            "output_dir": str(base / raw["data"]["output_dir"]),
            "intelligence_db": str(base / "data/intelligence.db"),
        },
        "market": {
            "snapshot_providers": raw["data"]["snapshot_providers"],
            **raw["screening"],
            "factor_weights": raw["factor_weights"],
        },
        "intelligence": {
            "first_run_lookback_hours": 48, "resume_overlap_hours": 6,
            "cluster_window_hours": 72, "max_items_per_source": 30,
            "max_scout_events": 40, "max_deep_events": 5,
            "max_company_hypotheses": 3, "full_text_max_chars": 50000,
            "title_similarity_threshold": 88, "source_fetch_timeout_seconds": 20,
            "publish_leads_when_ai_unavailable": True,
            **INTELLIGENCE_DEFAULTS,
        },
        "quality": dict(QUALITY_DEFAULTS),
        "llm": {
            "provider": "openai_compatible", "base_url": "https://api.deepseek.com",
            "api_key_env": "DEEPSEEK_API_KEY", "prompt_version": "tech-intel-v2",
            "scout": {
                "model": "deepseek-v4-flash", "max_output_tokens": 6000,
                "temperature": 0,
                "extra_body": {
                    "thinking": {"type": "disabled"}, "reasoning_effort": "low",
                },
            },
            "analyst": {
                "model": "deepseek-v4-pro", "max_output_tokens": 6000,
                "temperature": 0,
                "extra_body": {
                    "thinking": {"type": "enabled"}, "reasoning_effort": "high",
                },
            },
            "verifier": {
                "model": "deepseek-v4-pro", "max_output_tokens": 3000,
                "temperature": 0,
                "extra_body": {
                    "thinking": {"type": "enabled"}, "reasoning_effort": "high",
                },
            },
        },
    }


def _validate(settings: dict[str, Any]) -> None:
    for section in (
        "app", "paths", "market", "intelligence", "quality", "llm", "topics", "sources",
    ):
        if section not in settings:
            raise ValueError(f"配置缺少 {section} 段")
    weights = settings["market"]["factor_weights"]
    if set(weights) != WEIGHT_KEYS or abs(sum(float(v) for v in weights.values()) - 1.0) > 1e-9:
        raise ValueError("market.factor_weights 必须包含六个标准因子且权重之和为 1")
    topic_ids = [item["id"] for item in settings["topics"]]
    if len(topic_ids) != len(set(topic_ids)):
        raise ValueError("topics.yaml 中存在重复主题 id")
    intelligence = settings["intelligence"]
    selection_weights = (
        float(intelligence["selection_deterministic_weight"]),
        float(intelligence["selection_model_weight"]),
    )
    if any(value < 0 for value in selection_weights) or abs(sum(selection_weights) - 1.0) > 1e-9:
        raise ValueError("intelligence 的确定性与模型筛选权重必须非负且之和为 1")
    quality = settings["quality"]
    for minimum, maximum in (
        ("min_key_facts", "max_key_facts"),
        ("min_supported_evidence", "max_supported_evidence"),
        ("min_risks", "max_risks"),
        ("min_counterpoints", "max_counterpoints"),
    ):
        if int(quality[minimum]) < 0 or int(quality[minimum]) > int(quality[maximum]):
            raise ValueError(f"quality.{minimum} 必须位于 0 到 quality.{maximum} 之间")
    for key in (
        "max_single_source_confidence", "max_deep_confidence", "lead_confidence_cap",
    ):
        if not 0 <= float(quality[key]) <= 1:
            raise ValueError(f"quality.{key} 必须位于 0 到 1 之间")


def resolve_path(settings: dict[str, Any], key: str) -> Path:
    value = Path(settings["paths"][key])
    return value.resolve() if value.is_absolute() else (Path(settings["_config_dir"]) / value).resolve()
