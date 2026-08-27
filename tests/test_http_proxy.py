from __future__ import annotations

import requests

from daily_intel.infrastructure.http import prefers_direct, proxy_attempts, request_with_fallback


class FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code


def test_chinese_hosts_prefer_direct_then_proxy(monkeypatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7897")
    assert prefers_direct("push2.eastmoney.com")
    attempts = proxy_attempts("https://push2.eastmoney.com/api")
    assert attempts[0] == {}
    assert attempts[1]["https"] == "http://127.0.0.1:7897"


def test_overseas_hosts_prefer_proxy_then_direct(monkeypatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    attempts = proxy_attempts("https://export.arxiv.org/api/query")
    assert attempts[0]["https"] == "http://127.0.0.1:7897"
    assert attempts[1] == {}


def test_request_falls_back_when_proxy_fails(monkeypatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    seen: list[dict] = []

    def original(session, method, url, **kwargs):
        seen.append(dict(session.proxies))
        if session.proxies:
            raise requests.exceptions.ProxyError("blocked")
        return FakeResponse(200)

    session = requests.Session()
    response = request_with_fallback(
        session, original, "GET", "https://export.arxiv.org/api/query",
    )
    assert response.status_code == 200
    assert seen[0]["https"] == "http://127.0.0.1:7897"
    assert seen[1] == {}


def test_direct_first_host_does_not_need_proxy(monkeypatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    seen: list[dict] = []

    def original(session, method, url, **kwargs):
        seen.append(dict(session.proxies))
        return FakeResponse(200)

    session = requests.Session()
    request_with_fallback(session, original, "GET", "https://push2.eastmoney.com/x")
    assert seen == [{}]
