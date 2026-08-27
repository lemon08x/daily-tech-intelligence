from __future__ import annotations

import contextlib
import io
from collections.abc import Callable, Sequence
from datetime import datetime

import akshare as ak
import pandas as pd

from daily_intel.infrastructure.http import http_get, install_proxy_fallback
from daily_intel.market.cache import CsvCache, Dataset
from daily_intel.market.normalize import combine_news_frames

SINA_GLOBAL_INDEX_SYMBOLS = (
    "int_dji", "int_nasdaq", "int_sp500", "int_nikkei", "int_hangseng", "int_ftse",
)


def fetch_sina_global_indices() -> pd.DataFrame:
    url = "https://hq.sinajs.cn/list=" + ",".join(SINA_GLOBAL_INDEX_SYMBOLS)
    response = http_get(
        url,
        timeout=20,
        headers={
            "User-Agent": "DailyIntel/0.3 (+local research digest)",
            "Referer": "https://finance.sina.com.cn",
        },
    )
    response.raise_for_status()
    response.encoding = "gb18030"
    rows: list[dict[str, str]] = []
    for line in response.text.splitlines():
        if "=\"" not in line:
            continue
        left, _, right = line.partition("=")
        code = left.rsplit("_", 1)[-1].strip()
        payload = right.strip().rstrip(";").strip('"')
        parts = payload.split(",")
        if len(parts) < 4 or not parts[0].strip():
            continue
        rows.append({
            "代码": code,
            "名称": parts[0].strip(),
            "最新价": parts[1].strip(),
            "涨跌幅": parts[3].strip(),
        })
    if not rows:
        raise ValueError("新浪全球指数返回空数据")
    return pd.DataFrame(rows)


class AkShareProvider:
    def __init__(self, cache: CsvCache, now: datetime, offline: bool = False) -> None:
        self.cache, self.now, self.offline = cache, now, offline
        install_proxy_fallback()

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

    def _fetch_combined(
        self,
        key: str,
        providers: Sequence[tuple[str, Callable[[], pd.DataFrame]]],
        optional: bool = False,
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
                parts.append(frame)
                sources.append(source)
            except Exception as exc:
                errors.append(f"{source}: {type(exc).__name__}: {exc}")
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
        return self._fetch_combined(
            "news",
            [
                ("AkShare / 同花顺快讯", ak.stock_info_global_ths),
                ("AkShare / 新浪财经快讯", ak.stock_info_global_sina),
            ],
            True,
        )

    def trading_calendar(self) -> Dataset:
        return self._fetch("trading_calendar", [("AkShare / 新浪交易日历", ak.tool_trade_date_hist_sina)], True)

    def global_indices(self) -> Dataset:
        return self._fetch(
            "global_indices",
            [
                ("Sina / 全球指数", fetch_sina_global_indices),
                ("AkShare / 东方财富全球指数", ak.index_global_spot_em),
            ],
            True,
        )

    def global_futures(self) -> Dataset:
        return self._fetch(
            "global_futures",
            [("AkShare / 东方财富国际期货", ak.futures_global_spot_em)],
            True,
        )
