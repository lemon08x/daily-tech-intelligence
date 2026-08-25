from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


WEIGHT_KEYS = {"momentum", "value", "liquidity", "activity", "daily_strength", "size"}


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
        },
        "llm": {
            "provider": "openai_compatible", "base_url": "https://api.deepseek.com",
            "api_key_env": "DEEPSEEK_API_KEY", "prompt_version": "tech-intel-v1",
            "scout": {"model": "deepseek-v4-flash", "thinking": False, "reasoning_effort": "low", "max_output_tokens": 6000},
            "analyst": {"model": "deepseek-v4-pro", "thinking": True, "reasoning_effort": "high", "max_output_tokens": 6000},
            "verifier": {"model": "deepseek-v4-pro", "thinking": True, "reasoning_effort": "high", "max_output_tokens": 3000},
        },
    }


def _validate(settings: dict[str, Any]) -> None:
    for section in ("app", "paths", "market", "intelligence", "llm", "topics", "sources"):
        if section not in settings:
            raise ValueError(f"配置缺少 {section} 段")
    weights = settings["market"]["factor_weights"]
    if set(weights) != WEIGHT_KEYS or abs(sum(float(v) for v in weights.values()) - 1.0) > 1e-9:
        raise ValueError("market.factor_weights 必须包含六个标准因子且权重之和为 1")
    topic_ids = [item["id"] for item in settings["topics"]]
    if len(topic_ids) != len(set(topic_ids)):
        raise ValueError("topics.yaml 中存在重复主题 id")


def resolve_path(settings: dict[str, Any], key: str) -> Path:
    value = Path(settings["paths"][key])
    return value.resolve() if value.is_absolute() else (Path(settings["_config_dir"]) / value).resolve()
