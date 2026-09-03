from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from daily_intel.core.ports import SourceAdapter
from daily_intel.intelligence.sources.curated import HuggingFaceDailyPapersSource
from daily_intel.intelligence.sources.feeds import ArxivSource, FeedSource
from daily_intel.intelligence.sources.github_issues import GitHubIssuesSource
from daily_intel.intelligence.sources.sitemaps import SitemapSource


def load_weekly_blog_feeds(path: Path, limit: int = 12) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    feeds: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        identity = str(item.get("id") or "")
        if not identity or not url.startswith("https://"):
            continue
        feeds.append({
            "id": identity,
            "name": str(item.get("name") or identity),
            "url": url,
            "tier": int(item.get("tier") or 3),
            "lane": "general",
            "publisher_id": str(item.get("publisher_id") or "weekly_blog_pool"),
            "max_items": int(item.get("max_items") or 5),
            "fetch_full_text": False,
            "unshorten": True,
            "content_type": "article",
        })
        if len(feeds) >= max(1, limit):
            break
    return feeds


def iter_source_configs(config: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for item in config.get("arxiv_sources", []):
        if item and item.get("enabled", True):
            yield "arxiv", item
    for item in config.get("feeds", []):
        if item.get("enabled", True):
            yield "feed", item
    for item in config.get("sitemaps", []):
        if item.get("enabled", True):
            yield "sitemap", item
    for item in config.get("apis", []):
        if item.get("enabled", True):
            yield str(item.get("type", "")), item
    for item in config.get("github_releases", []):
        if item.get("enabled", True):
            yield "github_release", item


def configured_source_count(config: dict[str, Any]) -> int:
    return sum(1 for _ in iter_source_configs(config))


def build_sources(
    config: dict[str, Any], timeout: int, extra_feeds: list[dict[str, Any]] | None = None,
) -> list[SourceAdapter]:
    sources: list[SourceAdapter] = []
    seen_ids = {str(item.get("id")) for _, item in iter_source_configs(config)}
    for source_type, item in iter_source_configs(config):
        if source_type == "arxiv":
            sources.append(ArxivSource(item, timeout))
        elif source_type == "feed":
            sources.append(FeedSource(item, timeout))
        elif source_type == "sitemap":
            sources.append(SitemapSource(item, timeout))
        elif source_type == "huggingface_daily_papers":
            sources.append(HuggingFaceDailyPapersSource(item, timeout))
        elif source_type == "github_issues":
            sources.append(GitHubIssuesSource(item, timeout))
        elif source_type == "github_release":
            feed_config = {
                **item,
                "url": f"https://github.com/{item['repo']}/releases.atom",
                "fetch_full_text": False,
                "content_type": "github_release",
            }
            sources.append(FeedSource(feed_config, timeout))
        else:
            raise ValueError(f"Unsupported source type: {source_type or '<empty>'}")
    for item in extra_feeds or []:
        identity = str(item.get("id") or "")
        if not identity or identity in seen_ids:
            continue
        seen_ids.add(identity)
        sources.append(FeedSource(item, timeout))
    return sources
