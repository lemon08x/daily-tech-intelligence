from __future__ import annotations

from typing import Any

from daily_intel.core.models import Analysis
from daily_intel.market.normalize import clean_text


MOOD_EFFECT = {
    "偏强": "赚钱效应较强",
    "回暖": "赚钱效应不错",
    "分化": "涨跌参半",
    "偏弱": "赚钱效应偏弱",
}

PREFERRED_INDEX_CODES = ("sh000001", "sz399001", "sz399006", "sh000300", "sh000905")


def _takeaway(analysis: Analysis) -> str:
    if analysis.plain_takeaway.strip():
        return analysis.plain_takeaway.strip()
    for fact in analysis.key_facts:
        if fact.strip():
            return fact.strip()
    return analysis.headline.strip()


def _signed_percent(value: Any) -> str:
    from daily_intel.publication.reporting import format_number

    number = format_number(value)
    if number == "—":
        return number
    prefix = "+" if float(value) > 0 else ""
    return f"{prefix}{number}%"


def _index_line(row: dict[str, Any]) -> str:
    from daily_intel.publication.reporting import format_number

    name = str(row.get("name") or "").strip() or "指数"
    price = format_number(row.get("price"))
    return f"{name} {price}（{_signed_percent(row.get('pct_change'))}）"


def _index_lines(index_records: list[dict[str, Any]]) -> list[str]:
    by_code = {
        str(row.get("code") or "").lower(): row
        for row in index_records
        if row.get("name") and row.get("price") is not None
    }
    ordered: list[str] = []
    seen: set[str] = set()
    for code in PREFERRED_INDEX_CODES:
        row = by_code.get(code)
        if row is None:
            continue
        ordered.append(_index_line(row))
        seen.add(code)
    for row in index_records:
        code = str(row.get("code") or "").lower()
        if code in seen or not row.get("name") or row.get("price") is None:
            continue
        ordered.append(_index_line(row))
    return ordered[:5]


def _news_threads(news_records: list[dict[str, Any]], limit: int = 5) -> list[dict[str, str]]:
    threads: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in news_records:
        title = clean_text(str(item.get("title") or ""), 80)
        if not title:
            continue
        key = title.casefold()
        if key in seen:
            continue
        seen.add(key)
        summary = clean_text(str(item.get("summary") or ""), 90)
        threads.append({
            "title": title,
            "summary": summary,
            "url": str(item.get("url") or ""),
            "line": f"{title}。{summary}" if summary else title,
        })
        if len(threads) >= limit:
            break
    return threads


def build_plain_digest(
    analyses: list[Analysis], context: dict[str, Any],
) -> dict[str, Any]:
    """Assemble a reader-first brief from structured fields; no extra model call."""
    from daily_intel.publication.reporting import format_money

    breadth = context.get("breadth") or {}
    mood = str(breadth.get("mood") or "分化")
    advancing = int(breadth.get("advancing") or 0)
    declining = int(breadth.get("declining") or 0)
    amount = format_money(breadth.get("amount_cny"))
    effect = MOOD_EFFECT.get(mood, "涨跌互现")
    index_lines = _index_lines(list(context.get("index_records") or []))
    lead_index = index_lines[0] if index_lines else ""
    market_line = (
        f"大盘{mood}：{lead_index + '，' if lead_index else ''}"
        f"全市场成交 {amount}，上涨 {advancing} 家、下跌 {declining} 家，{effect}。"
    )
    hot = [
        str(row.get("name") or "").strip()
        for row in (context.get("hot_industry_records") or [])[:3]
        if str(row.get("name") or "").strip()
    ]
    tech_items = []
    for analysis in analyses:
        url = analysis.evidence[0].url if analysis.evidence else ""
        tech_items.append({
            "headline": analysis.headline,
            "takeaway": _takeaway(analysis),
            "url": url,
            "status": analysis.status.value,
        })
    return {
        "tech_items": tech_items,
        "market_line": market_line,
        "index_lines": index_lines,
        "hot_industries": hot,
        "news_threads": _news_threads(list(context.get("news_records") or [])),
        "has_content": bool(tech_items or lead_index or advancing or declining),
    }
