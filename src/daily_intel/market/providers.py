from __future__ import annotations

import contextlib
import io
from collections.abc import Callable, Sequence
from datetime import datetime

import akshare as ak
import pandas as pd

from daily_intel.infrastructure.http import install_proxy_fallback
from daily_intel.market.cache import CsvCache, Dataset
from daily_intel.market.normalize import combine_news_frames


class AkShareProvider:
    def __init__(self, cache: CsvCache, now: datetime, offline: bool = False) -> None:
        self.cache, self.now, self.offline = cache, now, offline
        install_proxy_fallback()

    def _fetch(
        self,
        key: str,
        providers: Sequence[tuple[str, Callable[[], pd.DataFrame]]],
        optional: bool = False,
        combine: bool = False,
    ) -> Dataset:
        if self.offline:
            cached = self.cache.load(key, "离线模式：未请求实时接口")
            if cached:
                return cached
            if optional:
                return Dataset(key, pd.DataFrame(), "无", self.now.isoformat(), True, "没有可用缓存")
            raise RuntimeError(f"离线模式下没有 {key} 缓存")
        parts: list[pd.DataFrame] = []
        sources: list[str] = []
        errors: list[str] = []
        for source, callback in providers:
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    frame = callback()
                if not isinstance(frame, pd.DataFrame) or frame.empty:
                    raise ValueError("接口返回空表")
                if not combine:
                    fetched_at = self.now.isoformat(timespec="seconds")
                    self.cache.save(key, frame, source, fetched_at)
                    return Dataset(key, frame, source, fetched_at)
                parts.append(frame)
                sources.append(source)
            except Exception as exc:
                errors.append(f"{source}: {type(exc).__name__}: {exc}")
        if combine:
            combined = combine_news_frames(parts)
            if not combined.empty:
                fetched_at = self.now.isoformat(timespec="seconds")
                source = " + ".join(sources)
                self.cache.save(key, combined, source, fetched_at)
                return Dataset(key, combined, source, fetched_at)
        message = " | ".join(errors)
        cached = self.cache.load(key, message)
        if cached:
            return cached
        if optional:
            return Dataset(key, pd.DataFrame(), "无", self.now.isoformat(), True, message)
        raise RuntimeError(f"{key} 的实时接口和缓存均不可用。{message}")

    def news(self) -> Dataset:
        return self._fetch(
            "news",
            [
                ("AkShare / 同花顺快讯", ak.stock_info_global_ths),
                ("AkShare / 新浪财经快讯", ak.stock_info_global_sina),
            ],
            True,
            combine=True,
        )

    def trading_calendar(self) -> Dataset:
        return self._fetch("trading_calendar", [("AkShare / 新浪交易日历", ak.tool_trade_date_hist_sina)], True)
