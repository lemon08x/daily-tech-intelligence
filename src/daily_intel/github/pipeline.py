from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from daily_intel.core.progress import progress
from daily_intel.github.trending import (
    fetch_github_project_context,
    fetch_github_stars,
    fetch_trending,
    format_stars,
    merge_trending,
)
from daily_intel.market.normalize import clean_text


@dataclass(slots=True)
class GitRunResult:
    projects: list[dict[str, Any]]
    source_status: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _fallback_plain(item: dict[str, Any]) -> str:
    description = clean_text(str(item.get("description") or ""), 160)
    if description:
        return description
    name = item.get("full_name") or "该项目"
    return f"{name} 是一个正在 GitHub 热门榜上的开源项目，页面没有说明它解决什么问题。"


def apply_git_brief(brief: Any, projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name = {
        str(item.full_name).lower(): item
        for item in (getattr(brief, "items", None) or [])
        if getattr(item, "full_name", "")
    }
    for project in projects:
        match = by_name.get(str(project.get("full_name") or "").lower())
        if match is None:
            project["plain"] = project.get("plain") or _fallback_plain(project)
            continue
        if str(match.kicker or "").strip():
            project["kicker"] = str(match.kicker).strip()
        project["plain"] = clean_text(match.function, 200)
    return projects


def annotate_github_visuals(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    peak_today = max((int(item.get("stars_today") or 0) for item in projects), default=0) or 1
    peak_week = max((int(item.get("stars_week") or 0) for item in projects), default=0) or 1
    for index, item in enumerate(projects, 1):
        today = int(item.get("stars_today") or 0)
        week = int(item.get("stars_week") or 0)
        item["rank"] = index
        item["kicker"] = item.get("kicker") or "开源"
        item["plain"] = item.get("plain") or _fallback_plain(item)
        item["today_width"] = round(min(100.0, today / peak_today * 100), 1) if today else 0.0
        item["week_width"] = round(min(100.0, week / peak_week * 100), 1) if week else 0.0
        item["stars_total_label"] = format_stars(item.get("stars_total") or 0)
    return projects


class GitHubTrendingPipeline:
    """Hottest and fastest-growing GitHub projects."""

    def __init__(self, settings: dict[str, Any], cache_dir: Path) -> None:
        self.settings = settings
        self.config = settings.get("github") or {}
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def run(self, now: datetime) -> GitRunResult:
        if not bool(self.config.get("enabled", True)):
            return GitRunResult(projects=[], source_status=[], errors=[])
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
                if period == "daily":
                    daily = cached
                else:
                    weekly = cached
                status.append({
                    "name": f"github_trending_{period}", "source": label,
                    "fetched_at": now.isoformat(timespec="seconds"), "stale": True,
                    "count": len(cached), "error": message,
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
        for item in projects:
            if int(item.get("stars_total") or 0) <= 0:
                try:
                    item["stars_total"] = fetch_github_stars(str(item.get("full_name") or ""), timeout=timeout)
                except Exception:
                    item["stars_total"] = 0
            progress(f"当前：读取 {item.get('full_name') or '仓库'} 的 README…")
            try:
                context = fetch_github_project_context(str(item.get("full_name") or ""), timeout=timeout)
            except Exception:
                context = {"readme": "", "root_files": "", "manifest": ""}
            item["readme"] = context.get("readme") or ""
            item["root_files"] = context.get("root_files") or ""
            item["manifest"] = context.get("manifest") or ""
        briefs = annotate_github_visuals(projects)
        return GitRunResult(projects=briefs, source_status=status, errors=errors)

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
