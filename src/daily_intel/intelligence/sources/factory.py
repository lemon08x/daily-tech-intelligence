from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from daily_intel.core.ports import SourceAdapter
from daily_intel.intelligence.sources.curated import HuggingFaceDailyPapersSource
from daily_intel.intelligence.sources.feeds import ArxivSource, FeedSource
from daily_intel.intelligence.sources.sitemaps import SitemapSource


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


def build_sources(config: dict[str, Any], timeout: int) -> list[SourceAdapter]:
    sources: list[SourceAdapter] = []
    for source_type, item in iter_source_configs(config):
        if source_type == "arxiv":
            sources.append(ArxivSource(item, timeout))
        elif source_type == "feed":
            sources.append(FeedSource(item, timeout))
        elif source_type == "sitemap":
            sources.append(SitemapSource(item, timeout))
        elif source_type == "huggingface_daily_papers":
            sources.append(HuggingFaceDailyPapersSource(item, timeout))
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
    return sources
