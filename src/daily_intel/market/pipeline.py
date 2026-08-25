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
    normalize_indices,
    normalize_industries,
    normalize_news,
    normalize_snapshot,
)
from daily_intel.market.providers import AkShareProvider
from daily_intel.market.scoring import market_breadth, screen_and_score


@dataclass(slots=True)
class MarketRunResult:
    snapshot: pd.DataFrame
    candidates: pd.DataFrame
    radar_news: pd.DataFrame
    context: dict[str, Any]
    metadata: dict[str, Any]


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    clean = frame.replace({float("nan"): None})
    return json.loads(clean.to_json(orient="records", force_ascii=False))


def _select_news(
    news: pd.DataFrame, keywords: list[str], limit: int, include_unmatched: bool = False
) -> pd.DataFrame:
    if news.empty:
        return news
    frame = news.copy()
    searchable = (frame["title"].fillna("") + " " + frame["summary"].fillna("")).str.lower()
    lowered = [keyword.lower() for keyword in keywords if keyword]
    frame["relevance"] = searchable.apply(lambda text: sum(keyword in text for keyword in lowered))
    frame["tags"] = searchable.apply(
        lambda text: "、".join(keyword for keyword in keywords if keyword.lower() in text)[:80]
    )
    frame["summary"] = frame["summary"].map(lambda value: clean_text(value, 180))
    frame = frame.drop_duplicates(subset=["title"], keep="first")
    relevant = frame[frame["relevance"].gt(0)].sort_values("relevance", ascending=False)
    if include_unmatched and len(relevant) < limit:
        supplement = frame.loc[~frame.index.isin(relevant.index)]
        relevant = pd.concat([relevant, supplement], ignore_index=True)
    return relevant.head(limit).reset_index(drop=True)


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
    """Deterministic market pipeline; intelligence output never enters this score path."""

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
        market_config = config["market"]
        snapshot_data = self.provider.snapshot(market_config["snapshot_providers"])
        industry_data = self.provider.industries()
        index_data = self.provider.indices()
        news_data = self.provider.news()
        calendar_data = self.provider.trading_calendar()

        snapshot = normalize_snapshot(snapshot_data.frame)
        screening = {
            key: value for key, value in market_config.items()
            if key not in {"factor_weights", "snapshot_providers"}
        }
        candidates, universe = screen_and_score(
            snapshot, screening, market_config["factor_weights"]
        )
        top_candidates = candidates.head(int(config["app"]["top_stocks"])).copy()
        industries = normalize_industries(industry_data.frame)
        indices = normalize_indices(index_data.frame)
        news = normalize_news(news_data.frame)
        hot = industries.head(int(config["app"]["top_industries"]))
        weak = industries.tail(int(config["app"]["weak_industries"])).sort_values("pct_change")

        # This is retained only as a low-weight market radar and legacy report section.
        keywords = [
            "A股", "沪指", "深证", "创业板", "央行", "证监会", "政策", "经济",
            "产业", "人民币", "融资", "AI", "半导体",
        ]
        keywords.extend(hot["name"].astype(str).tolist())
        keywords.extend(top_candidates["name"].astype(str).tolist())
        selected_news = _select_news(news, keywords, int(config["app"]["top_news"]))
        breadth = market_breadth(snapshot)
        market_date, is_trading_day = _market_date(calendar_data.frame, self.now)
        source_status = [
            _status(item)
            for item in (snapshot_data, industry_data, index_data, news_data, calendar_data)
        ]

        context = {
            "market_date": market_date,
            "is_trading_day": is_trading_day,
            "breadth": breadth,
            "eligible_count": int(universe["eligible"].sum()),
            "candidate_records": _records(top_candidates),
            "hot_industry_records": _records(hot),
            "weak_industry_records": _records(weak),
            "index_records": _records(indices),
            "news_records": _records(selected_news),
            "market_source_status": source_status,
            "weights": market_config["factor_weights"],
        }
        metadata = {
            "market_date": market_date,
            "is_trading_day": is_trading_day,
            "snapshot_rows": int(len(snapshot)),
            "eligible_rows": int(len(candidates)),
            "screening": screening,
            "factor_weights": market_config["factor_weights"],
            "sources": source_status,
        }
        return MarketRunResult(snapshot, candidates, news, context, metadata)
