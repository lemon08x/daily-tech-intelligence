from __future__ import annotations

from typing import Any

from daily_intel.core.models import Analysis, DigestBrief
from daily_intel.market.normalize import clean_text
from daily_intel.publication.plain_digest import _scan_line, _signed_percent


def digest_brief_payload(
    analyses: list[Analysis],
    context: dict[str, Any],
    industry_bars: list[dict[str, Any]],
    board: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "tech": [
            {
                "headline": item.headline,
                "takeaway": item.plain_takeaway or _scan_line(item),
                "lane": item.lane,
            }
            for item in analyses[:8]
        ],
        "market_movers": [
            {
                "name": item.get("name") or item.get("label"),
                "change": item.get("label") or item.get("label_change"),
            }
            for item in [*industry_bars, *board]
        ],
        "market_news": [
            {
                "title": item.get("title") or "",
                "summary": item.get("summary") or "",
                "url": item.get("url") or "",
            }
            for item in list(context.get("news_records") or [])[:8]
        ],
    }


def fallback_scan_paragraph(
    analyses: list[Analysis],
    industry_bars: list[dict[str, Any]],
    board: list[dict[str, Any]],
    news_records: list[dict[str, Any]],
) -> str:
    parts: list[str] = []
    tech = [_scan_line(item) for item in analyses if _scan_line(item)]
    if tech:
        parts.append("科技方面，" + "；".join(tech[:4]))
    movers: list[str] = []
    for item in industry_bars:
        name = str(item.get("name") or "").strip()
        label = str(item.get("label") or _signed_percent(item.get("pct_change")))
        if name:
            movers.append(f"{name} {label}")
    for item in board:
        name = str(item.get("label") or "").strip()
        label = str(item.get("label_change") or _signed_percent(item.get("pct_change")))
        if name:
            movers.append(f"{name} {label}")
    news_titles = [
        clean_text(str(item.get("title") or ""), 48)
        for item in news_records
        if item.get("title")
    ]
    market_bits: list[str] = []
    if movers:
        market_bits.append("、".join(movers[:6]))
    if news_titles:
        market_bits.append("同时有" + "；".join(news_titles[:3]))
    if market_bits:
        parts.append("市场上，" + "。".join(market_bits))
    text = "。".join(part.rstrip("。") for part in parts if part).strip()
    return (text + "。") if text else ""


def fallback_market_news(item: dict[str, Any]) -> dict[str, Any]:
    title = clean_text(str(item.get("title") or ""), 120)
    summary = clean_text(str(item.get("summary") or ""), 240)
    haystack = f"{title} {summary}".strip()
    quote = title if len(title) >= 8 else haystack[:80]
    return {
        **item,
        "impact": item.get("impact") or (
            "来源只交代了事件本身，没有给出可核对的量化影响，因此只能把它当作产业或交易预期的线索。"
        ),
        "consequences": item.get("consequences") or (
            "后续要看细则是否落地、供给或禁令是否兑现，以及相关行业有没有跟进公告；现在不能把涨跌说成已经发生的结果。"
        ),
        "reasoning": item.get("reasoning") or (
            f"从标题「{title or '该事件'}」能判断这是公开事件线索；摘要没有更多机制或数据，所以影响和后果都停在可观察的下一步，而不是价格预测。"
        ),
        "quotes": list(item.get("quotes") or ([quote] if len(quote) >= 8 else [])),
    }


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
    analyses: list[Analysis],
    context: dict[str, Any],
    industry_bars: list[dict[str, Any]],
    board: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    news_records = [dict(item) for item in list(context.get("news_records") or [])]
    paragraph = clean_text(str(context.get("scan_paragraph") or ""), 900)
    if brief and brief.scan_paragraph.strip():
        paragraph = clean_text(brief.scan_paragraph, 900)
    if not paragraph:
        paragraph = fallback_scan_paragraph(analyses, industry_bars, board, news_records)

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
        enriched.append({
            **item,
            "impact": clean_text(match.impact, 400),
            "consequences": clean_text(match.consequences, 400),
            "reasoning": clean_text(match.reasoning, 600),
            "quotes": _quotes_from_source(match.quotes, str(item.get("title") or ""), summary),
        })
    return paragraph, enriched
