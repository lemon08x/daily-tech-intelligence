from __future__ import annotations

from typing import Any

from daily_intel.core.models import DigestBrief
from daily_intel.market.normalize import _clean_url, clean_text
from daily_intel.publication.plain_digest import match_kicker


def digest_brief_payload(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "market_news": [
            {
                "title": item.get("title") or "",
                "summary": item.get("summary") or "",
                "url": item.get("url") or "",
            }
            for item in list(context.get("news_records") or [])[:8]
        ],
    }


def _one_sentence(value: str) -> str:
    text = clean_text(value, 160).strip()
    for mark in ("。", "！", "？", ".", "!"):
        index = text.find(mark)
        if index >= 8:
            return text[: index + 1]
    return text


def present_market_news(item: dict[str, Any]) -> dict[str, Any]:
    title = clean_text(str(item.get("title") or ""), 120)
    scan = clean_text(str(item.get("scan") or ""), 160) or _one_sentence(title)
    kicker = clean_text(str(item.get("kicker") or ""), 8) or match_kicker(
        f"{title} {scan}"
    ) or "市场"
    return {**item, "kicker": kicker, "scan": scan, "url": _clean_url(item.get("url"))}


def fallback_market_news(item: dict[str, Any]) -> dict[str, Any]:
    title = clean_text(str(item.get("title") or ""), 120)
    summary = clean_text(str(item.get("summary") or ""), 160)
    return present_market_news({
        **item,
        "kicker": item.get("kicker") or match_kicker(f"{title} {summary}") or "市场",
        "scan": item.get("scan") or _one_sentence(title) or _one_sentence(summary) or title,
    })


def apply_digest_brief(
    brief: DigestBrief | None,
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    news_records = [dict(item) for item in list(context.get("news_records") or [])]
    analyzed = {
        clean_text(item.title, 120): item
        for item in (brief.market_news if brief else [])
    }
    enriched: list[dict[str, Any]] = []
    for item in news_records:
        title = clean_text(str(item.get("title") or ""), 120)
        match = analyzed.get(title)
        if match is None:
            enriched.append(fallback_market_news(item))
            continue
        enriched.append(present_market_news({
            **item,
            "kicker": match.kicker,
            "scan": match.scan,
        }))
    return enriched
