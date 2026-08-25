from __future__ import annotations

import contextlib
import io
from collections.abc import Callable, Sequence
from datetime import datetime

import akshare as ak
import pandas as pd

from daily_intel.market.cache import CsvCache, Dataset


class AkShareProvider:
    def __init__(self, cache: CsvCache, now: datetime, offline: bool = False) -> None:
        self.cache, self.now, self.offline = cache, now, offline

    def _fetch(self, key: str, providers: Sequence[tuple[str, Callable[[], pd.DataFrame]]], optional: bool = False) -> Dataset:
        if self.offline:
            cached = self.cache.load(key, "离线模式：未请求实时接口")
            if cached:
                return cached
            if optional:
                return Dataset(key, pd.DataFrame(), "无", self.now.isoformat(), True, "没有可用缓存")
            raise RuntimeError(f"离线模式下没有 {key} 缓存")
        errors = []
        for source, callback in providers:
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    frame = callback()
                if not isinstance(frame, pd.DataFrame) or frame.empty:
                    raise ValueError("接口返回空表")
                fetched_at = self.now.isoformat(timespec="seconds")
                self.cache.save(key, frame, source, fetched_at)
                return Dataset(key, frame, source, fetched_at)
            except Exception as exc:
                errors.append(f"{source}: {type(exc).__name__}: {exc}")
        message = " | ".join(errors)
        cached = self.cache.load(key, message)
        if cached:
            return cached
        if optional:
            return Dataset(key, pd.DataFrame(), "无", self.now.isoformat(), True, message)
        raise RuntimeError(f"{key} 的实时接口和缓存均不可用。{message}")

    def snapshot(self, preferred: Sequence[str]) -> Dataset:
        choices = {"tencent": ("AkShare / 腾讯证券", ak.stock_zh_a_spot_tx), "sina": ("AkShare / 新浪财经", ak.stock_zh_a_spot)}
        providers = [choices[name] for name in preferred if name in choices]
        if not providers:
            raise ValueError("market.snapshot_providers 至少包含 tencent 或 sina")
        return self._fetch("stock_snapshot", providers)

    def industries(self) -> Dataset:
        return self._fetch("industries", [("AkShare / 新浪行业", lambda: ak.stock_sector_spot(indicator="新浪行业"))], True)

    def indices(self) -> Dataset:
        return self._fetch("indices", [("AkShare / 新浪指数", ak.stock_zh_index_spot_sina)], True)

    def news(self) -> Dataset:
        return self._fetch("news", [("AkShare / 同花顺快讯", ak.stock_info_global_ths), ("AkShare / 新浪财经快讯", ak.stock_info_global_sina)], True)

    def trading_calendar(self) -> Dataset:
        return self._fetch("trading_calendar", [("AkShare / 新浪交易日历", ak.tool_trade_date_hist_sina)], True)
