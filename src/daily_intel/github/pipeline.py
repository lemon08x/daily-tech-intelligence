from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from daily_intel.core.progress import progress
from daily_intel.github.trending import (
    append_catalog,
    fetch_gitlab_projects,
    fetch_huggingface_models,
    fetch_trending,
    merge_trending,
)
from daily_intel.market.normalize import clean_text


@dataclass(slots=True)
class GitRunResult:
    projects: list[dict[str, Any]]
    chart: dict[str, Any] = field(default_factory=dict)
    source_status: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _fallback_plain(item: dict[str, Any]) -> str:
    description = clean_text(str(item.get("description") or ""), 160)
    language = str(item.get("language") or "").strip()
    origin = str(item.get("origin_label") or "开源平台")
    if description:
        prefix = f"这是一个{language}项目：" if language else f"这是一个来自{origin}的项目："
        return prefix + description
    name = item.get("full_name") or "该项目"
    return f"{name} 正在 {origin} 热门榜上，页面没有给出项目简介。"


def _fallback_scenario(item: dict[str, Any]) -> tuple[str, str]:
    name = str(item.get("full_name") or "该仓库")
    hint = clean_text(str(item.get("description") or item.get("plain") or ""), 72)
    if hint:
        scene = (
            f"假设一名工程师手头任务和「{hint}」有关，打开 {name} 的 README 和示例，"
            "按仓库说明跑通最小用例，再判断能不能接到自己的流程里。"
            "这是根据简介做的使用场景模拟，不是实测记录。"
        )
    else:
        scene = (
            f"假设有人看到 {name} 登上热门榜，打开仓库说明，按官方示例跑通最小用例，"
            "用来判断它能不能解决自己手头的问题。这是场景推演，不是实测记录。"
        )
    return "使用场景模拟", scene


def _spark(width: float, active: bool) -> str:
    if not active:
        return "░░░░░░░░"
    filled = max(1, int(round(width / 12.5)))
    filled = min(8, filled)
    return "█" * filled + "░" * (8 - filled)


def annotate_github_visuals(projects: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    peak_today = max((int(item.get("stars_today") or 0) for item in projects), default=0) or 1
    peak_week = max((int(item.get("stars_week") or 0) for item in projects), default=0) or 1
    languages = Counter(
        (str(item.get("language") or "").strip() or "未标注") for item in projects
    )
    peak_lang = max(languages.values(), default=1)
    language_bars = [
        {
            "name": name,
            "count": count,
            "width": round(count / peak_lang * 100, 1),
            "label": f"{count} 个",
            "spark": _spark(count / peak_lang * 100, True),
        }
        for name, count in languages.most_common()
    ]
    hottest = max(projects, key=lambda item: int(item.get("stars_today") or 0), default=None)
    fastest = max(projects, key=lambda item: int(item.get("stars_week") or 0), default=None)
    for index, item in enumerate(projects, 1):
        today = int(item.get("stars_today") or 0)
        week = int(item.get("stars_week") or 0)
        item["rank"] = index
        item["today_width"] = round(min(100.0, today / peak_today * 100), 1) if today else 0.0
        item["week_width"] = round(min(100.0, week / peak_week * 100), 1) if week else 0.0
        item["today_spark"] = _spark(item["today_width"], today > 0)
        item["week_spark"] = _spark(item["week_width"], week > 0)
    chart = {
        "count": len(projects),
        "language_count": len(languages),
        "max_stars_today": int(hottest.get("stars_today") or 0) if hottest else 0,
        "max_stars_week": int(fastest.get("stars_week") or 0) if fastest else 0,
        "hottest_name": hottest.get("full_name") if hottest and int(hottest.get("stars_today") or 0) else "",
        "fastest_name": fastest.get("full_name") if fastest and int(fastest.get("stars_week") or 0) else "",
        "language_bars": language_bars,
    }
    return projects, chart


class GitHubTrendingPipeline:
    """Hottest and fastest-growing GitHub projects, explained in plain language."""

    def __init__(self, settings: dict[str, Any], cache_dir: Path) -> None:
        self.settings = settings
        self.config = settings.get("github") or {}
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def run(self, now: datetime, stages: Any | None = None, ai_enabled: bool = False) -> GitRunResult:
        if not bool(self.config.get("enabled", True)):
            return GitRunResult(projects=[], chart={}, source_status=[], errors=[])
        timeout = int(self.settings.get("intelligence", {}).get("source_fetch_timeout_seconds", 20))
        errors: list[str] = []
        status: list[dict[str, Any]] = []
        daily, weekly = [], []
        for period, label in (("daily", "GitHub Trending 今日"), ("weekly", "GitHub Trending 本周")):
            try:
                progress(f"当前：拉取 {label}…")
                rows = fetch_trending(period, timeout=timeout)
                if not rows:
                    raise ValueError("页面没有解析到项目")
                if period == "daily":
                    daily = rows
                else:
                    weekly = rows
                status.append({
                    "name": f"github_trending_{period}", "source": label,
                    "fetched_at": now.isoformat(timespec="seconds"), "stale": False,
                    "count": len(rows), "error": "",
                })
            except Exception as exc:
                message = f"{label}: {type(exc).__name__}: {exc}"
                errors.append(message)
                cached = self._load_cache(period)
                if cached:
                    if period == "daily":
                        daily = cached
                    else:
                        weekly = cached
                    status.append({
                        "name": f"github_trending_{period}", "source": label,
                        "fetched_at": now.isoformat(timespec="seconds"), "stale": True,
                        "count": len(cached), "error": message,
                    })
                else:
                    status.append({
                        "name": f"github_trending_{period}", "source": label,
                        "fetched_at": now.isoformat(timespec="seconds"), "stale": True,
                        "count": 0, "error": message,
                    })
        if daily:
            self._save_cache("daily", daily)
        if weekly:
            self._save_cache("weekly", weekly)
        projects = merge_trending(
            daily, weekly,
            daily_limit=int(self.config.get("daily_limit", 8)),
            weekly_limit=int(self.config.get("weekly_limit", 8)),
            publish_limit=int(self.config.get("publish_limit", 10)),
        )
        extras: list[dict[str, Any]] = []
        catalog = (
            ("huggingface", "Hugging Face 热门模型", int(self.config.get("huggingface_limit", 4)), fetch_huggingface_models),
            ("gitlab", "GitLab 高星项目", int(self.config.get("gitlab_limit", 3)), fetch_gitlab_projects),
        )
        for cache_key, label, limit, fetcher in catalog:
            if limit <= 0:
                continue
            try:
                progress(f"当前：拉取 {label}…")
                rows = fetcher(limit=limit, timeout=timeout)
                if not rows:
                    raise ValueError("没有解析到项目")
                extras.extend(rows)
                self._save_cache(cache_key, rows)
                status.append({
                    "name": f"{cache_key}_trending", "source": label,
                    "fetched_at": now.isoformat(timespec="seconds"), "stale": False,
                    "count": len(rows), "error": "",
                })
            except Exception as exc:
                message = f"{label}: {type(exc).__name__}: {exc}"
                errors.append(message)
                cached = self._load_cache(cache_key)
                extras.extend(cached)
                status.append({
                    "name": f"{cache_key}_trending", "source": label,
                    "fetched_at": now.isoformat(timespec="seconds"), "stale": True,
                    "count": len(cached), "error": message,
                })
        projects = append_catalog(projects, extras)
        briefs = self._brief(projects, stages, ai_enabled, errors)
        briefs, chart = annotate_github_visuals(briefs)
        return GitRunResult(projects=briefs, chart=chart, source_status=status, errors=errors)

    def _brief(
        self, projects: list[dict[str, Any]], stages: Any | None, ai_enabled: bool, errors: list[str],
    ) -> list[dict[str, Any]]:
        by_name = {item["full_name"]: item for item in projects}
        if ai_enabled and stages is not None and projects:
            try:
                progress(f"当前：为 {len(projects)} 个仓库生成解说和使用场景…")
                payload = [
                    {
                        "full_name": item["full_name"],
                        "description": item.get("description") or "",
                        "language": item.get("language") or "",
                        "origin": item.get("origin_label") or item.get("origin") or "GitHub",
                        "reason": item.get("reason") or "",
                        "stars_today": item.get("stars_today") or 0,
                        "stars_week": item.get("stars_week") or 0,
                    }
                    for item in projects
                ]
                batch = stages.brief_github(payload)
                for item in batch.items:
                    target = by_name.get(item.full_name)
                    if target is None or not item.plain.strip():
                        continue
                    target["kicker"] = (item.kicker or "开源").strip()[:4]
                    target["plain"] = clean_text(item.plain, 220)
                    if item.scenario.strip():
                        target["scenario_title"] = clean_text(item.scenario_title or "使用场景模拟", 40)
                        target["scenario"] = clean_text(item.scenario, 420)
            except Exception as exc:
                errors.append(f"git_brief: {type(exc).__name__}: {exc}")
        for item in projects:
            item.setdefault("kicker", "开源")
            item.setdefault("plain", _fallback_plain(item))
            if not item.get("scenario"):
                title, scene = _fallback_scenario(item)
                item["scenario_title"] = title
                item["scenario"] = scene
        return projects

    def _cache_path(self, period: str) -> Path:
        return self.cache_dir / f"github_trending_{period}.json"

    def _save_cache(self, period: str, rows: list[dict[str, Any]]) -> None:
        self._cache_path(period).write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    def _load_cache(self, period: str) -> list[dict[str, Any]]:
        path = self._cache_path(period)
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return payload if isinstance(payload, list) else []
