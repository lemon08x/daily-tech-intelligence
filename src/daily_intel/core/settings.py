from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


WEIGHT_KEYS = {"momentum", "value", "liquidity", "activity", "daily_strength", "size"}
QUALITY_DEFAULTS = {
    "policy_version": "evidence-gate-v2",
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
    "plain_takeaway_max_chars": 280,
}
INTELLIGENCE_DEFAULTS = {
    "selection_deterministic_weight": .65,
    "selection_model_weight": .35,
    "selection_model_reject_floor": 55,
    "selection_repeat_penalty": .4,
    "selection_repeat_hours": 36,
    "max_general_events": 5,
    "max_hardcore_events": 5,
}
GITHUB_DEFAULTS = {
    "enabled": True,
    "daily_limit": 8,
    "weekly_limit": 8,
    "publish_limit": 10,
    "huggingface_limit": 4,
    "gitlab_limit": 3,
}


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_settings(path: Path) -> dict[str, Any]:
    path = path.resolve()
    raw = _read_yaml(path)
    config_dir = path.parent
    required = {"app", "paths", "market", "intelligence", "llm"}
    missing = sorted(required.difference(raw))
    if missing:
        raise ValueError(
            "settings.yaml 配置缺少当前版本必需段: " + ", ".join(missing)
        )
    for key, value in INTELLIGENCE_DEFAULTS.items():
        raw["intelligence"].setdefault(key, value)
    github = raw.setdefault("github", {})
    for key, value in GITHUB_DEFAULTS.items():
        github.setdefault(key, value)
    quality = raw.setdefault("quality", {})
    for key, value in QUALITY_DEFAULTS.items():
        quality.setdefault(key, value)
    raw["topics"] = _read_yaml(config_dir / "topics.yaml").get("topics", [])
    raw["sources"] = _read_yaml(config_dir / "sources.yaml")
    raw["_config_path"] = path
    raw["_config_dir"] = config_dir
    raw["_project_root"] = config_dir.parent
    _validate(raw)
    return raw


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
    _validate_sources(settings["sources"])
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


def _validate_sources(sources: dict[str, Any]) -> None:
    entries: list[tuple[str, dict[str, Any]]] = []
    arxiv_groups = sources.get("arxiv_sources", [])
    entries.extend(("arxiv", item) for item in arxiv_groups if item)
    for section, source_type in (
        ("feeds", "feed"),
        ("sitemaps", "sitemap"),
        ("apis", "api"),
        ("github_releases", "github_release"),
    ):
        entries.extend((source_type, item) for item in sources.get(section, []))

    ids = [str(item.get("id", "")).strip() for _, item in entries]
    if any(not item for item in ids):
        raise ValueError("sources.yaml 中每个来源都必须有非空 id")
    if len(ids) != len(set(ids)):
        raise ValueError("sources.yaml 中存在重复来源 id")
    for source_type, item in entries:
        tier = int(item.get("tier", 0))
        if tier not in {1, 2, 3}:
            raise ValueError(f"来源 {item['id']} 的 tier 必须为 1、2 或 3")
        if source_type == "arxiv" and not item.get("categories"):
            raise ValueError(f"arXiv 来源 {item['id']} 缺少 categories")
        if source_type in {"feed", "sitemap"} and not str(item.get("url", "")).startswith("https://"):
            raise ValueError(f"来源 {item['id']} 必须使用 HTTPS URL")
        if source_type == "api" and item.get("type") not in {"huggingface_daily_papers"}:
            raise ValueError(f"来源 {item['id']} 使用了不支持的 API 类型")
        if source_type == "github_release" and "/" not in str(item.get("repo", "")):
            raise ValueError(f"GitHub 来源 {item['id']} 的 repo 格式无效")


def resolve_path(settings: dict[str, Any], key: str) -> Path:
    value = Path(settings["paths"][key])
    return value.resolve() if value.is_absolute() else (Path(settings["_config_dir"]) / value).resolve()
