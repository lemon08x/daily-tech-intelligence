"""Probe representative tech and market sources with proxy, direct, and fallback."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from daily_intel.core.settings import load_settings  # noqa: E402
from daily_intel.infrastructure.http import (  # noqa: E402
    env_proxy_url,
    http_get,
    install_proxy_fallback,
    proxy_attempts,
)
from daily_intel.intelligence.sources.common import USER_AGENT  # noqa: E402
from daily_intel.intelligence.sources.factory import iter_source_configs  # noqa: E402

TIMEOUT = 8

MARKET_TARGETS = [
    ("market:tencent_spot", "https://qt.gtimg.cn/q=sh000001"),
    ("market:sina_index", "https://hq.sinajs.cn/list=s_sh000001"),
    ("market:eastmoney_global", "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=1"),
    ("market:eastmoney_futures", "https://futsseapi.eastmoney.com/list/COMEX?pageSize=1&pageIndex=0"),
    ("market:ths_news", "https://news.10jqka.com.cn/"),
]


def _targets() -> list[tuple[str, str]]:
    settings = load_settings(PROJECT_ROOT / "config" / "settings.yaml")
    items = list(MARKET_TARGETS)
    for source_type, config in iter_source_configs(settings["sources"]):
        url = str(config.get("url") or "")
        if source_type == "arxiv":
            url = "https://export.arxiv.org/api/query?search_query=cat:cs.AI&start=0&max_results=1"
        elif source_type == "github_release":
            url = f"https://github.com/{config['repo']}/releases.atom"
        elif source_type == "huggingface_daily_papers":
            url = config.get("url") or "https://huggingface.co/api/daily_papers"
        if url:
            items.append((str(config["id"]), url))
    return items


def _try(url: str, proxies: dict[str, str] | None) -> str:
    session = requests.Session()
    session.trust_env = False
    session.headers["User-Agent"] = USER_AGENT
    try:
        response = session.get(url, timeout=TIMEOUT, proxies=proxies or {})
        if response.status_code < 400 and response.content:
            return f"OK {response.status_code} {len(response.content)}B"
        return f"HTTP {response.status_code}"
    except Exception as exc:
        return f"{type(exc).__name__}"


def main() -> int:
    install_proxy_fallback()
    proxy = env_proxy_url()
    print(f"proxy={proxy or '(none)'}")
    print(f"{'source':<28} {'proxy':<22} {'direct':<22} {'fallback':<22}")
    failures = 0
    for name, url in _targets():
        proxy_result = _try(url, {"http": proxy, "https": proxy} if proxy else None)
        direct_result = _try(url, {})
        try:
            response = http_get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
            fallback = f"OK {response.status_code} {len(response.content)}B" if response.content else f"HTTP {response.status_code}"
            if response.status_code >= 400:
                failures += 1
        except Exception as exc:
            fallback = type(exc).__name__
            failures += 1
        if "OK" not in fallback:
            failures += 0
        print(f"{name:<28} {proxy_result:<22} {direct_result:<22} {fallback:<22}")
        print(f"  {url[:110]}")
        print(f"  attempts={proxy_attempts(url)}")
    print(f"fallback_failures={failures}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
