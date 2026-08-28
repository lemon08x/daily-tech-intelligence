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
TAG_RE = re.compile(r"<[^>]+>")
TRENDING_HEADERS = {
    "User-Agent": "DailyIntel/0.4 (+local research digest)",
    "Accept": "text/html,application/xhtml+xml",
}


def _count(match: re.Match[str] | None) -> int:
    if match is None:
        return 0
    return int(match.group(1).replace(",", ""))


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


def parse_huggingface_models(payload: object, *, limit: int = 4) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(payload, list):
        return rows
    for item in payload:
        if not isinstance(item, dict):
            continue
        full_name = str(item.get("modelId") or item.get("id") or "").strip()
        if not full_name or "/" not in full_name:
            continue
        likes = int(item.get("likes") or 0)
        rows.append({
            "full_name": full_name,
            "url": f"https://huggingface.co/{full_name}",
            "description": clean_text(str(item.get("pipeline_tag") or item.get("library_name") or "机器学习模型"), 220),
            "language": str(item.get("pipeline_tag") or "").strip(),
            "origin": "huggingface",
            "origin_label": "Hugging Face",
            "stars_today": 0,
            "stars_week": likes,
            "delta": likes,
            "reason": "Hugging Face 热门模型",
        })
        if len(rows) >= max(1, limit):
            break
    return rows


def parse_gitlab_projects(payload: object, *, limit: int = 3) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(payload, list):
        return rows
    for item in payload:
        if not isinstance(item, dict):
            continue
        full_name = str(item.get("path_with_namespace") or "").strip()
        url = str(item.get("web_url") or "").strip()
        if not full_name or not url:
            continue
        stars = int(item.get("star_count") or 0)
        rows.append({
            "full_name": full_name,
            "url": url,
            "description": clean_text(str(item.get("description") or ""), 220),
            "language": "",
            "origin": "gitlab",
            "origin_label": "GitLab",
            "stars_today": 0,
            "stars_week": stars,
            "delta": stars,
            "reason": "GitLab 高星项目",
        })
        if len(rows) >= max(1, limit):
            break
    return rows


def fetch_huggingface_models(limit: int = 4, timeout: int = 20) -> list[dict[str, Any]]:
    response = http_get(
        f"https://huggingface.co/api/models?sort=trending&limit={max(1, limit)}",
        timeout=timeout,
        headers={"User-Agent": TRENDING_HEADERS["User-Agent"], "Accept": "application/json"},
    )
    response.raise_for_status()
    return parse_huggingface_models(response.json(), limit=limit)


def fetch_gitlab_projects(limit: int = 3, timeout: int = 20) -> list[dict[str, Any]]:
    response = http_get(
        "https://gitlab.com/api/v4/projects?order_by=star_count&sort=desc&simple=true"
        f"&per_page={max(1, limit)}&visibility=public",
        timeout=timeout,
        headers={"User-Agent": TRENDING_HEADERS["User-Agent"], "Accept": "application/json"},
    )
    response.raise_for_status()
    return parse_gitlab_projects(response.json(), limit=limit)


def append_catalog(
    base: list[dict[str, Any]], extra: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen = {str(item.get("full_name") or "").lower() for item in base}
    merged = list(base)
    for item in extra:
        name = str(item.get("full_name") or "").lower()
        if not name or name in seen:
            continue
        seen.add(name)
        merged.append(dict(item))
    return merged
