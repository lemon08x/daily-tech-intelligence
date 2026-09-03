from __future__ import annotations

import base64
import html as html_lib
import os
import re
from typing import Any
from urllib.parse import quote

from daily_intel.infrastructure.http import http_get
from daily_intel.market.normalize import clean_text


ARTICLE_RE = re.compile(r"<article class=\"Box-row\"[\s\S]*?</article>", re.I)
REPO_HEADING_RE = re.compile(
    r'<h2\b[^>]*>[\s\S]*?<a\b[^>]*href="(/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"',
    re.I,
)
DESC_RE = re.compile(r"<p\b[^>]*>([\s\S]*?)</p>", re.I)
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
        repository = REPO_HEADING_RE.search(article)
        if repository is None:
            continue
        full_name = repository.group(1).lstrip("/")
        if full_name in seen:
            continue
        seen.add(full_name)
        description_match = DESC_RE.search(article)
        description = html_lib.unescape(
            TAG_RE.sub("", description_match.group(1) if description_match else "")
        )
        language_match = LANG_RE.search(article)
        language = html_lib.unescape(
            language_match.group(1) if language_match else ""
        ).strip()
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


def _github_headers(accept: str = "application/vnd.github+json") -> dict[str, str]:
    headers = {
        "User-Agent": TRENDING_HEADERS["User-Agent"],
        "Accept": accept,
    }
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or ""
    if token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    return headers


def fetch_github_stars(full_name: str, timeout: int = 12) -> int:
    response = http_get(
        f"https://api.github.com/repos/{full_name}",
        timeout=timeout,
        headers=_github_headers("application/json"),
    )
    response.raise_for_status()
    payload = response.json()
    return int((payload or {}).get("stargazers_count") or 0)


_README_BADGE = re.compile(r"!\[[^\]]*]\([^)]+\)")
MANIFEST_NAMES = (
    "package.json", "pyproject.toml", "Cargo.toml", "go.mod",
    "composer.json", "setup.cfg", "setup.py", "Gemfile",
)
SOURCE_DIR_NAMES = ("src", "app", "lib", "cmd", "packages", "scripts")
SOURCE_SUFFIXES = (
    ".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".rb",
    ".php", ".java", ".kt", ".cs", ".sh",
)
SOURCE_ENTRY_NAMES = (
    "main.py", "app.py", "cli.py", "server.py", "index.js", "index.ts",
    "index.tsx", "main.rs", "main.go",
)
README_NAMES = ("README.md", "README.rst", "README.txt", "README")


def _clip_block(text: str, max_chars: int) -> str:
    cleaned = re.sub(r"<!--.*?-->", " ", text or "", flags=re.S)
    cleaned = _README_BADGE.sub("", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if max_chars <= 0 or len(cleaned) <= max_chars:
        return cleaned
    cut = cleaned[:max_chars]
    if "\n" in cut:
        cut = cut.rsplit("\n", 1)[0]
    return cut.strip()


def _decode_contents_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    if payload.get("encoding") == "base64" and payload.get("content"):
        try:
            return base64.b64decode(payload["content"]).decode("utf-8", errors="replace")
        except (ValueError, TypeError):
            return ""
    return str(payload.get("content") or "")


def fetch_github_readme(full_name: str, timeout: int = 12, max_chars: int = 4500) -> str:
    identity = str(full_name or "").strip("/")
    if not identity or identity.count("/") != 1:
        return ""
    api_response = None
    api_error: Exception | None = None
    try:
        api_response = http_get(
            f"https://api.github.com/repos/{identity}/readme",
            timeout=timeout,
            headers=_github_headers("application/vnd.github.raw"),
        )
        if api_response.status_code == 200:
            return _clip_block(api_response.text, max_chars)
    except Exception as exc:
        api_error = exc

    # Anonymous GitHub API quotas are shared by proxy egress IPs and can be
    # exhausted even when the public repository itself is reachable. Raw
    # content is the stable read-only fallback and does not need API quota.
    encoded_identity = quote(identity, safe="/")
    for name in README_NAMES:
        try:
            response = http_get(
                f"https://raw.githubusercontent.com/{encoded_identity}/HEAD/{name}",
                timeout=timeout,
                headers=TRENDING_HEADERS,
            )
            if response.status_code == 200 and response.text.strip():
                return _clip_block(response.text, max_chars)
        except Exception:
            continue
    if api_response is not None and api_response.status_code != 404:
        api_response.raise_for_status()
    if api_error is not None:
        raise api_error
    return ""


def _fetch_github_file(full_name: str, path: str, timeout: int, max_chars: int) -> str:
    response = http_get(
        f"https://api.github.com/repos/{full_name}/contents/{quote(path, safe='/')}",
        timeout=timeout,
        headers=_github_headers(),
    )
    if response.status_code == 404:
        return ""
    response.raise_for_status()
    return _clip_block(_decode_contents_payload(response.json()), max_chars)


def _source_candidate(entries: Any) -> tuple[str, str]:
    if not isinstance(entries, list):
        return "", ""
    files = [
        item for item in entries
        if isinstance(item, dict)
        and str(item.get("type") or "") == "file"
        and str(item.get("name") or "").lower().endswith(SOURCE_SUFFIXES)
        and str(item.get("name") or "").lower() not in {
            name.lower() for name in MANIFEST_NAMES
        }
    ]
    files.sort(key=lambda item: (
        str(item.get("name") or "").lower() not in SOURCE_ENTRY_NAMES,
        len(str(item.get("path") or item.get("name") or "")),
        str(item.get("path") or item.get("name") or "").lower(),
    ))
    entry_files = [
        item for item in files
        if str(item.get("name") or "").lower() in SOURCE_ENTRY_NAMES
    ]
    if entry_files:
        chosen = entry_files[0]
        return str(chosen.get("path") or chosen.get("name") or ""), ""
    directories = {
        str(item.get("name") or "").lower(): str(item.get("path") or item.get("name") or "")
        for item in entries
        if isinstance(item, dict) and str(item.get("type") or "") == "dir"
    }
    for name in SOURCE_DIR_NAMES:
        if name in directories:
            return "", directories[name]
    if files:
        chosen = files[0]
        return str(chosen.get("path") or chosen.get("name") or ""), ""
    return "", ""


def _fetch_source_excerpt(
    full_name: str, root_entries: Any, timeout: int, max_chars: int = 1800,
) -> str:
    path, directory = _source_candidate(root_entries)
    if not path and directory:
        response = http_get(
            f"https://api.github.com/repos/{full_name}/contents/{quote(directory, safe='/')}",
            timeout=timeout,
            headers=_github_headers(),
        )
        if response.status_code != 404:
            response.raise_for_status()
            path, _ = _source_candidate(response.json())
    if not path:
        return ""
    content = _fetch_github_file(full_name, path, timeout, max_chars)
    return f"{path}:\n{content}" if content else ""


def fetch_github_project_context(full_name: str, timeout: int = 12) -> dict[str, str]:
    """Collect bounded README, manifest, directory and source-entry evidence."""
    identity = str(full_name or "").strip("/")
    empty = {"readme": "", "root_files": "", "manifest": "", "source_excerpt": ""}
    if not identity or identity.count("/") != 1:
        return empty
    readme = ""
    try:
        readme = fetch_github_readme(identity, timeout=timeout)
    except Exception:
        readme = ""
    root_files = ""
    manifest = ""
    source_excerpt = ""
    try:
        response = http_get(
            f"https://api.github.com/repos/{identity}/contents/",
            timeout=timeout,
            headers=_github_headers(),
        )
        if response.status_code != 404:
            response.raise_for_status()
            entries = response.json()
            names = [
                str(item.get("name") or "")
                for item in entries
                if isinstance(item, dict) and item.get("name")
            ]
            root_files = " ".join(names[:40])
            chosen = next((name for name in MANIFEST_NAMES if name in names), "")
            if chosen:
                manifest = _fetch_github_file(identity, chosen, timeout, 1500)
            source_excerpt = _fetch_source_excerpt(identity, entries, timeout)
    except Exception:
        pass
    return {
        "readme": readme,
        "root_files": root_files,
        "manifest": manifest,
        "source_excerpt": source_excerpt,
    }
