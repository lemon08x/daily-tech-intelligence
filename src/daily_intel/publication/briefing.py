from __future__ import annotations

from typing import Any

from daily_intel.core.models import DigestBrief
from daily_intel.market.normalize import clean_text


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


def present_market_news(item: dict[str, Any]) -> dict[str, Any]:
    impact = clean_text(str(item.get("impact") or ""), 700)
    extra = clean_text(str(item.get("consequences") or ""), 400)
    if extra and extra not in impact:
        impact = f"{impact} {extra}".strip() if impact else extra
    reasoning = clean_text(str(item.get("reasoning") or ""), 800)
    quotes = [clean_text(str(quote), 240) for quote in list(item.get("quotes") or []) if str(quote).strip()]
    for quote in quotes:
        marker = f"原文：「{quote}」"
        if quote and marker not in reasoning:
            reasoning = f"{reasoning} {marker}".strip() if reasoning else marker
    return {**item, "impact_text": impact, "basis_text": reasoning}


def fallback_market_news(item: dict[str, Any]) -> dict[str, Any]:
    title = clean_text(str(item.get("title") or ""), 120)
    summary = clean_text(str(item.get("summary") or ""), 240)
    haystack = f"{title} {summary}".strip()
    quote = title if len(title) >= 8 else haystack[:80]
    quotes = list(item.get("quotes") or ([quote] if len(quote) >= 8 else []))
    return present_market_news({
        **item,
        "impact": item.get("impact") or (
            "来源只交代了事件本身，没有给出可核对的量化影响，后续要看细则是否落地、供给或禁令是否兑现；现在不能把涨跌说成已经发生的结果。"
        ),
        "consequences": item.get("consequences") or "",
        "reasoning": item.get("reasoning") or (
            f"从标题「{title or '该事件'}」能判断这是公开事件线索；摘要没有更多机制或数据，所以影响停在可观察的下一步。"
        ),
        "quotes": quotes,
    })


def _quotes_from_source(quotes: list[str], title: str, summary: str) -> list[str]:
    haystack = f"{title} {summary}"
    kept: list[str] = []
    seen: set[str] = set()
    for quote in quotes:
        text = clean_text(quote, 240)
        if len(text) < 8 or text in seen or text not in haystack:
            continue
        seen.add(text)
        kept.append(text)
        if len(kept) >= 3:
            break
    if not kept:
        title_text = clean_text(title, 80)
        if len(title_text) >= 8:
            kept.append(title_text)
    return kept


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
        summary = str(item.get("summary") or "")
        enriched.append(present_market_news({
            **item,
            "impact": clean_text(match.impact, 700),
            "consequences": clean_text(getattr(match, "consequences", "") or "", 400),
            "reasoning": clean_text(match.reasoning, 800),
            "quotes": _quotes_from_source(match.quotes, str(item.get("title") or ""), summary),
        }))
    return enriched
