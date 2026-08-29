from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from daily_intel.core.ports import MarketProvider
from daily_intel.market.cache import CsvCache, Dataset
from daily_intel.market.normalize import (
    clean_text,
    normalize_global_quotes,
    normalize_indices,
    normalize_industries,
    normalize_news,
)
from daily_intel.market.providers import AkShareProvider


@dataclass(slots=True)
class MarketRunResult:
    radar_news: pd.DataFrame
    context: dict[str, Any]
    metadata: dict[str, Any]


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    clean = frame.replace({float("nan"): None})
    return json.loads(clean.to_json(orient="records", force_ascii=False))


A_SHARE_NEWS_TERMS = (
    "a股", "沪指", "上证", "深证", "创业板", "北证", "科创", "证监会", "央行",
    "人民币", "沪深",
)
GLOBAL_NEWS_TERMS = (
    "美股", "纳斯达克", "纳指", "标普", "道指", "道琼斯", "美联储", "欧央行",
    "日经", "恒生", "原油", "黄金", "铜", "美元", "美债", "欧股",
    "日本央行",
)
THEME_NEWS_TERMS = (
    "ai", "人工智能", "半导体", "芯片", "算力", "光模块", "gpu", "英伟达",
    "新能源", "锂电", "光伏", "机器人", "医药", "出海", "存储", "hbm",
)
CAUSE_NEWS_TERMS = (
    "政策", "监管", "标准", "制裁", "关税", "禁令", "管制", "禁运", "出口管制",
    "宣布", "出台", "实施", "叫停", "暂停", "恢复", "签署", "合作", "协议",
    "获批", "批准", "否决", "立案", "调查", "处罚", "召回", "事故", "停产",
    "投产", "量产", "突破", "加息", "降息", "美联储", "欧央行", "战争", "停火",
    "许可", "认证", "禁售", "限制", "中止", "投建", "开工", "落地",
    "设立", "成立", "发布", "采购", "短缺", "扩产", "并购", "收购", "中标",
    "投资", "建设", "实验室", "封锁", "扣押", "合同", "签订",
)
FLOW_NEWS_TERMS = (
    "北向", "南向", "成交额", "成交活跃", "融资余额", "两融", "主力资金",
    "净流入", "净流出", "涨停", "跌停", "高开", "低开", "翻红", "翻绿",
    "换手", "量能", "资金面", "赚钱效应",
)
EARNINGS_NEWS_TERMS = (
    "净利润", "营收", "中报", "年报", "分红", "业绩",
)
TICKER_ADMIN_NEWS_TERMS = (
    "风险警示", "股票简称", "证券简称", "简称变更", "简称变更为",
    "证券代码不变", "摘帽", "戴帽", "变更证券简称",
)


def _term_hits(text: str, terms: tuple[str, ...]) -> int:
    return sum(term.lower() in text for term in terms if term)


def is_earnings_news(text: str) -> bool:
    return _term_hits(text.lower(), EARNINGS_NEWS_TERMS) > 0


def is_ticker_admin_news(text: str) -> bool:
    return _term_hits(text.lower(), TICKER_ADMIN_NEWS_TERMS) > 0


def is_noise_news(text: str) -> bool:
    return is_flow_news(text) or is_earnings_news(text) or is_ticker_admin_news(text)


def is_event_cause_news(text: str) -> bool:
    lowered = text.lower()
    if is_earnings_news(lowered) or is_ticker_admin_news(lowered):
        return False
    return _term_hits(lowered, CAUSE_NEWS_TERMS) > 0


def is_flow_news(text: str) -> bool:
    lowered = text.lower()
    return _term_hits(lowered, FLOW_NEWS_TERMS) > 0 and not is_event_cause_news(lowered)


def rank_market_news(
    news: pd.DataFrame, extra_keywords: list[str], limit: int, min_fill: int = 5,
) -> pd.DataFrame:
    """Prefer event-cause news; if too few, fill with other important non-noise items."""
    if news.empty:
        return news
    frame = news.copy()
    searchable = (frame["title"].fillna("") + " " + frame["summary"].fillna("")).str.lower()
    extras = [keyword.lower() for keyword in extra_keywords if keyword]

    def event_score(text: str) -> int:
        if is_noise_news(text) or not is_event_cause_news(text):
            return 0
        return (
            _term_hits(text, CAUSE_NEWS_TERMS) * 3
            + _term_hits(text, A_SHARE_NEWS_TERMS) * 2
            + _term_hits(text, GLOBAL_NEWS_TERMS)
            + _term_hits(text, THEME_NEWS_TERMS)
            + sum(term in text for term in extras)
        )

    def fill_score(text: str) -> int:
        if is_noise_news(text) or is_event_cause_news(text) or not text.strip():
            return 0
        return (
            _term_hits(text, THEME_NEWS_TERMS) * 2
            + _term_hits(text, A_SHARE_NEWS_TERMS) * 2
            + _term_hits(text, GLOBAL_NEWS_TERMS)
            + sum(term in text for term in extras)
        )

    frame["relevance"] = searchable.map(event_score)
    frame["fill_score"] = searchable.map(fill_score)
    frame["tags"] = searchable.map(
        lambda text: "、".join(
            term for term in list(CAUSE_NEWS_TERMS) + extras
            if term.lower() in text
        )[:80]
    )
    frame["summary"] = frame["summary"].map(lambda value: clean_text(value, 180))
    frame = frame.drop_duplicates(subset=["title"], keep="first")
    ranked = frame[frame["relevance"].gt(0)].sort_values("relevance", ascending=False)
    need = max(0, min(limit, min_fill) - len(ranked))
    if need:
        fillers = frame.loc[
            ~frame.index.isin(ranked.index) & frame["fill_score"].gt(0)
        ].sort_values("fill_score", ascending=False).head(need)
        ranked = pd.concat([ranked, fillers], ignore_index=True)
    return ranked.head(limit).reset_index(drop=True)


def _market_date(calendar: pd.DataFrame, now: datetime) -> tuple[str, bool]:
    today = now.date()
    if calendar.empty or "trade_date" not in calendar.columns:
        return today.isoformat(), now.weekday() < 5
    dates = pd.to_datetime(calendar["trade_date"], errors="coerce").dropna().dt.date
    available = dates[dates <= today]
    if available.empty:
        return today.isoformat(), False
    latest = available.max()
    return latest.isoformat(), latest == today


def _status(dataset: Dataset) -> dict[str, Any]:
    return {
        "name": dataset.key,
        "source": dataset.source,
        "fetched_at": dataset.fetched_at,
        "stale": dataset.stale,
        "error": clean_text(dataset.error or "", 180),
    }


class MarketPipeline:
    """Deterministic market snapshot and news ranking; intelligence never enters this path."""

    def __init__(
        self,
        settings: dict[str, Any],
        cache_dir,
        now: datetime,
        offline: bool,
        provider: MarketProvider | None = None,
    ) -> None:
        self.settings = settings
        self.now = now
        self.provider = provider or AkShareProvider(
            CsvCache(cache_dir), now=now, offline=offline
        )

    def run(self) -> MarketRunResult:
        config = self.settings
        industry_data = self.provider.industries()
        index_data = self.provider.indices()
        news_data = self.provider.news()
        calendar_data = self.provider.trading_calendar()
        global_index_data = self.provider.global_indices()
        global_futures_data = self.provider.global_futures()

        industries = normalize_industries(industry_data.frame)
        indices = normalize_indices(index_data.frame)
        news = normalize_news(news_data.frame)
        hot = industries.head(int(config["app"]["top_industries"]))
        weak = industries.tail(int(config["app"]["weak_industries"])).sort_values("pct_change")
        global_indices = normalize_global_quotes(global_index_data.frame)
        commodities = normalize_global_quotes(global_futures_data.frame)

        extra_keywords = [
            "政策", "监管", "标准", "制裁",
            *hot["name"].astype(str).tolist(),
        ]
        selected_news = rank_market_news(news, extra_keywords, int(config["app"]["top_news"]))
        market_date, is_trading_day = _market_date(calendar_data.frame, self.now)
        source_status = [
            _status(item)
            for item in (
                industry_data, index_data, news_data, calendar_data,
                global_index_data, global_futures_data,
            )
        ]

        context = {
            "market_date": market_date,
            "is_trading_day": is_trading_day,
            "industry_records": _records(industries),
            "hot_industry_records": _records(hot),
            "weak_industry_records": _records(weak),
            "index_records": _records(indices),
            "global_index_records": _records(global_indices),
            "commodity_records": _records(commodities),
            "news_records": _records(selected_news),
            "market_source_status": source_status,
        }
        metadata = {
            "market_date": market_date,
            "is_trading_day": is_trading_day,
            "sources": source_status,
        }
        return MarketRunResult(news, context, metadata)
