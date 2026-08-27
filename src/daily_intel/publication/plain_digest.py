from __future__ import annotations

from typing import Any

from daily_intel.core.models import Analysis
from daily_intel.market.normalize import clean_text


INDUSTRY_MOVE_PCT = 2.0
INDEX_MOVE_PCT = 1.0
COMMODITY_MOVE_PCT = 1.5

INDEX_BOARD = (
    (("sh000001", "000001"), "A股", "上证", ("上证",)),
    (("sz399001", "399001"), "A股", "深证成指", ("深证成指", "深成指")),
    (("sz399006", "399006"), "A股", "创业板", ("创业板",)),
    (("sh000300", "000300"), "A股", "沪深300", ("沪深300",)),
    (("ndx", "nasdaq"), "美欧", "纳斯达克", ("纳斯达克", "纳指")),
    (("spx", "sp500"), "美欧", "标普500", ("标普",)),
    (("djia", "dji"), "美欧", "道琼斯", ("道琼斯", "道指")),
    (("hsi", "hangseng"), "亚太", "恒生", ("恒生",)),
    (("n225", "nikkei"), "亚太", "日经225", ("日经",)),
    (("ks11", "kospi"), "亚太", "韩国综指", ("韩国", "kospi")),
    (("gdaxi", "dax"), "美欧", "德国DAX", ("dax", "德国")),
    (("ftse",), "美欧", "英国富时", ("富时", "ftse", "伦敦")),
)

COMMODITY_BOARD = (
    ("黄金", "商品", "黄金"),
    ("原油", "商品", "原油"),
    ("铜", "商品", "铜"),
)


def _takeaway(analysis: Analysis) -> str:
    if analysis.plain_takeaway.strip():
        return analysis.plain_takeaway.strip()
    for fact in analysis.key_facts:
        if fact.strip():
            return fact.strip()
    return analysis.headline.strip()


def _one_sentence(value: str) -> str:
    text = clean_text(value, 400).strip()
    for mark in ("。", "！", "？"):
        index = text.find(mark)
        if index >= 8:
            return text[: index + 1]
    return text


def _scan_line(analysis: Analysis) -> str:
    return _one_sentence(_takeaway(analysis)) or clean_text(analysis.headline, 80)


def _signed_percent(value: Any) -> str:
    from daily_intel.publication.reporting import format_number

    number = format_number(value)
    if number == "—":
        return number
    prefix = "+" if float(value) > 0 else ""
    return f"{prefix}{number}%"


def _bare_code(value: Any) -> str:
    return str(value or "").lower().replace("sh", "").replace("sz", "").replace("bj", "")


def _match_quote(rows: list[dict[str, Any]], codes: tuple[str, ...], hints: tuple[str, ...]) -> dict[str, Any] | None:
    wanted = {_bare_code(item) for item in codes}
    for row in rows:
        if _bare_code(row.get("code")) in wanted:
            return row
    for row in rows:
        name = str(row.get("name") or "")
        if any(hint.lower() in name.lower() for hint in hints if hint):
            return row
    return None


def _news_why(aliases: tuple[str, ...], news_records: list[dict[str, Any]]) -> str:
    needles = [item.lower() for item in aliases if item]
    for item in news_records:
        haystack = f"{item.get('title') or ''} {item.get('summary') or ''}"
        if any(needle in haystack.lower() for needle in needles):
            return clean_text(str(item.get("title") or ""), 48)
    return ""


def _industry_bars(
    rows: list[dict[str, Any]], news_records: list[dict[str, Any]], limit: int = 4,
) -> list[dict[str, Any]]:
    picked: list[dict[str, Any]] = []
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name or row.get("pct_change") is None:
            continue
        change = float(row["pct_change"])
        why = _news_why((name,), news_records)
        if abs(change) < INDUSTRY_MOVE_PCT and not why:
            continue
        picked.append({
            "name": name,
            "pct_change": change,
            "leader": str(row.get("leader") or "").strip(),
            "why": why,
        })
        if len(picked) >= limit:
            break
    peak = max((abs(item["pct_change"]) for item in picked), default=1.0) or 1.0
    for item in picked:
        item["width"] = round(min(100.0, abs(item["pct_change"]) / peak * 100), 1)
        item["direction"] = "up" if item["pct_change"] >= 0 else "down"
        item["label"] = _signed_percent(item["pct_change"])
        filled = max(1, int(round(item["width"] / 12.5)))
        item["spark"] = "█" * filled + "░" * (8 - filled)
    return picked


def _board_item(region: str, label: str, row: dict[str, Any]) -> dict[str, Any]:
    change = row.get("pct_change")
    return {
        "region": region,
        "label": label,
        "price": row.get("price"),
        "pct_change": None if change is None else float(change),
        "direction": "up" if change is not None and float(change) >= 0 else "down",
        "label_change": _signed_percent(change),
    }


def build_market_board(context: dict[str, Any]) -> list[dict[str, Any]]:
    quotes = list(context.get("index_records") or []) + list(context.get("global_index_records") or [])
    news_records = list(context.get("news_records") or [])
    board: list[dict[str, Any]] = []
    seen: set[str] = set()
    for codes, region, label, hints in INDEX_BOARD:
        row = _match_quote(quotes, codes, hints)
        if row is None or label in seen:
            continue
        change = row.get("pct_change")
        why = _news_why((label, *hints), news_records)
        if change is None:
            continue
        if abs(float(change)) < INDEX_MOVE_PCT and not why:
            continue
        seen.add(label)
        item = _board_item(region, label, row)
        item["why"] = why
        board.append(item)
    commodities = list(context.get("commodity_records") or [])
    for hint, region, label in COMMODITY_BOARD:
        row = _match_quote(commodities, (), (hint,))
        if row is None or label in seen:
            continue
        change = row.get("pct_change")
        why = _news_why((label, hint), news_records)
        if change is None:
            continue
        if abs(float(change)) < COMMODITY_MOVE_PCT and not why:
            continue
        seen.add(label)
        item = _board_item(region, label, row)
        item["why"] = why
        board.append(item)
    return board


def build_plain_digest(
    analyses: list[Analysis], context: dict[str, Any],
) -> dict[str, Any]:
    """Compact scan board. Full takeaways stay on story cards; news stays in 市场情报."""
    tech_items = []
    for analysis in analyses:
        url = analysis.evidence[0].url if analysis.evidence else ""
        tech_items.append({
            "headline": analysis.headline,
            "scan": _scan_line(analysis),
            "takeaway": _takeaway(analysis),
            "url": url,
            "status": analysis.status.value,
        })
    news_records = list(context.get("news_records") or [])
    hot = list(context.get("hot_industry_records") or [])
    weak = list(reversed(list(context.get("weak_industry_records") or [])))
    industry_bars = _industry_bars(hot + weak, news_records)
    board = build_market_board(context)
    return {
        "tech_items": tech_items,
        "industry_bars": industry_bars,
        "board": board,
        "has_content": bool(tech_items or industry_bars or board),
    }
