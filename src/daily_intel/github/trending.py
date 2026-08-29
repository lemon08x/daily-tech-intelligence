from __future__ import annotations

import re
from typing import Any

from daily_intel.infrastructure.http import http_get
from daily_intel.market.normalize import clean_text


ARTICLE_RE = re.compile(r"<article class=\"Box-row\"[\s\S]*?</article>", re.I)
REPO_RE = re.compile(r'href="(/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"')
DESC_RE = re.compile(r"<p[^>]*>([\s\S]*?)</p>", re.I)
LANG_RE = re.compile(r'itemprop="programmingLanguage">([^<]+)', re.I)
STARS_TODAY_RE = re.compile(r"([\d,]+)\s+stars today", re.I)
STARS_WEEK_RE = re.compile(r"([\d,]+)\s+stars this week", re.I)
STARGAZERS_RE = re.compile(r'href="[^"]+/stargazers"[^>]*>([\s\S]*?)</a>', re.I)
TAG_RE = re.compile(r"<[^>]+>")
TRENDING_HEADERS = {
    "User-Agent": "DailyIntel/0.4 (+local research digest)",
    "Accept": "text/html,application/xhtml+xml",
}


def _count(match: re.Match[str] | None) -> int:
    if match is None:
        return 0
    return int(match.group(1).replace(",", ""))


def format_stars(value: Any) -> str:
    number = int(value or 0)
    if number <= 0:
        return ""
    if number >= 10000:
        text = f"{number / 10000:.1f}".rstrip("0").rstrip(".")
        return f"{text}万"
    return f"{number:,}"


def _stars_total_from_article(article: str) -> int:
    match = STARGAZERS_RE.search(article or "")
    if match is None:
        return 0
    digits = re.search(r"([\d,]+)", TAG_RE.sub("", match.group(1)))
    return _count(digits)


def parse_trending_html(html: str, period: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for article in ARTICLE_RE.findall(html or ""):
        hrefs = [item for item in REPO_RE.findall(article) if item.count("/") == 2]
        if not hrefs:
            continue
        full_name = hrefs[0].lstrip("/")
        if full_name in seen or full_name.startswith("topics/"):
            continue
        seen.add(full_name)
        description = TAG_RE.sub("", DESC_RE.search(article).group(1) if DESC_RE.search(article) else "")
        language = (LANG_RE.search(article).group(1) if LANG_RE.search(article) else "").strip()
        stars_today = _count(STARS_TODAY_RE.search(article))
        stars_week = _count(STARS_WEEK_RE.search(article))
        stars_total = _stars_total_from_article(article)
        delta = stars_today if period == "daily" else stars_week
        rows.append({
            "full_name": full_name,
            "url": f"https://github.com/{full_name}",
            "description": clean_text(description, 220),
            "language": language,
            "origin": "github",
            "origin_label": "GitHub",
            "period": period,
            "stars_today": stars_today,
            "stars_week": stars_week,
            "stars_total": stars_total,
            "delta": delta,
            "reason": "今日最热" if period == "daily" else "本周增长最快",
        })
    return rows


def fetch_trending(period: str, timeout: int = 20) -> list[dict[str, Any]]:
    since = "daily" if period == "daily" else "weekly"
    response = http_get(
        f"https://github.com/trending?since={since}",
        timeout=timeout,
        headers=TRENDING_HEADERS,
    )
    response.raise_for_status()
    return parse_trending_html(response.text, since)


def merge_trending(
    daily: list[dict[str, Any]],
    weekly: list[dict[str, Any]],
    *,
    daily_limit: int,
    weekly_limit: int,
    publish_limit: int,
) -> list[dict[str, Any]]:
    hottest = daily[: max(0, daily_limit)]
    fastest = weekly[: max(0, weekly_limit)]
    by_name: dict[str, dict[str, Any]] = {}
    for item in hottest:
        row = dict(item)
        row["reasons"] = [item["reason"]]
        by_name[item["full_name"]] = row
    for item in fastest:
        existing = by_name.get(item["full_name"])
        if existing is None:
            row = dict(item)
            row["reasons"] = [item["reason"]]
            by_name[item["full_name"]] = row
            continue
        if item["reason"] not in existing["reasons"]:
            existing["reasons"].append(item["reason"])
        existing["stars_week"] = max(int(existing.get("stars_week") or 0), int(item.get("stars_week") or 0))
        existing["stars_total"] = max(int(existing.get("stars_total") or 0), int(item.get("stars_total") or 0))
        existing["delta"] = max(int(existing.get("delta") or 0), int(item.get("delta") or 0))
    ranked = sorted(
        by_name.values(),
        key=lambda item: (len(item.get("reasons") or []), int(item.get("delta") or 0)),
        reverse=True,
    )
    picked = []
    for item in ranked[: max(1, publish_limit)]:
        row = dict(item)
        row["origin"] = row.get("origin") or "github"
        row["origin_label"] = row.get("origin_label") or "GitHub"
        row["reason"] = "、".join(row.get("reasons") or [row.get("reason") or "热门"])
        picked.append(row)
    return picked


def fetch_github_stars(full_name: str, timeout: int = 12) -> int:
    response = http_get(
        f"https://api.github.com/repos/{full_name}",
        timeout=timeout,
        headers={"User-Agent": TRENDING_HEADERS["User-Agent"], "Accept": "application/json"},
    )
    response.raise_for_status()
    payload = response.json()
    return int((payload or {}).get("stargazers_count") or 0)

