from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from daily_intel.core.ports import MarketProvider
from daily_intel.market.cache import CsvCache, Dataset
from daily_intel.market.normalize import clean_text, normalize_news
from daily_intel.market.providers import AkShareProvider


@dataclass(slots=True)
class MarketRunResult:
    radar_news: pd.DataFrame
    context: dict[str, Any]
    metadata: dict[str, Any]


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
    """Collect AkShare radar news and trading-day state for the unified Scout path."""

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
        news_data = self.provider.news()
        calendar_data = self.provider.trading_calendar()
        news = normalize_news(news_data.frame)
        market_date, is_trading_day = _market_date(calendar_data.frame, self.now)
        source_status = [_status(item) for item in (news_data, calendar_data)]
        context = {
            "market_date": market_date,
            "is_trading_day": is_trading_day,
            "market_source_status": source_status,
        }
        metadata = {
            "market_date": market_date,
            "is_trading_day": is_trading_day,
            "sources": source_status,
        }
        return MarketRunResult(news, context, metadata)
