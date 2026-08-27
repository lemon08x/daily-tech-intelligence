from __future__ import annotations

import os
from collections.abc import Callable
from urllib.parse import urlparse

import requests
from requests.exceptions import ChunkedEncodingError, ConnectionError, ProxyError, SSLError, Timeout


RETRYABLE = (ProxyError, SSLError, ConnectionError, Timeout, ChunkedEncodingError)
DIRECT_HOST_MARKERS = (
    "eastmoney.com", "sina.com.cn", "sina.cn", "sinajs.cn", "gtimg.cn", "qq.com",
    "10jqka.com.cn", "cninfo.com.cn", "sse.com.cn", "szse.cn",
    "hexun.com", "akfamily.xyz", "192.168.",
)
PROXY_ENV_KEYS = (
    "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
    "https_proxy", "http_proxy", "all_proxy",
)


def env_proxy_url() -> str:
    for key in PROXY_ENV_KEYS:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return ""


def prefers_direct(host: str) -> bool:
    hostname = (host or "").lower()
    return any(marker in hostname for marker in DIRECT_HOST_MARKERS)


def proxy_attempts(url: str) -> list[dict[str, str]]:
    proxy = env_proxy_url()
    direct: dict[str, str] = {}
    if not proxy:
        return [direct]
    via_proxy = {"http": proxy, "https": proxy}
    host = urlparse(url).hostname or ""
    if prefers_direct(host):
        return [direct, via_proxy]
    return [via_proxy, direct]


def request_with_fallback(
    session: requests.Session,
    original: Callable,
    method: str,
    url: str,
    **kwargs,
) -> requests.Response:
    if kwargs.get("proxies") is not None:
        return original(session, method, url, **kwargs)
    last_error: Exception | None = None
    saved_trust = session.trust_env
    saved_proxies = dict(session.proxies)
    try:
        for proxies in proxy_attempts(url):
            try:
                session.trust_env = False
                session.proxies = proxies
                response = original(session, method, url, **kwargs)
                if response.status_code in {407, 502, 503, 504} and len(proxy_attempts(url)) > 1:
                    last_error = requests.HTTPError(f"{response.status_code} via {'proxy' if proxies else 'direct'}")
                    continue
                return response
            except RETRYABLE as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise last_error
        return original(session, method, url, **kwargs)
    finally:
        session.trust_env = saved_trust
        session.proxies = saved_proxies


def install_proxy_fallback() -> None:
    original = requests.sessions.Session.request
    if getattr(original, "_daily_intel_proxy_fallback", False):
        return

    def wrapped(self, method, url, **kwargs):
        return request_with_fallback(self, original, method, url, **kwargs)

    wrapped._daily_intel_proxy_fallback = True  # type: ignore[attr-defined]
    requests.sessions.Session.request = wrapped  # type: ignore[method-assign]


def http_get(url: str, timeout: int, headers: dict[str, str] | None = None, **kwargs) -> requests.Response:
    install_proxy_fallback()
    return requests.get(url, timeout=timeout, headers=headers, **kwargs)
