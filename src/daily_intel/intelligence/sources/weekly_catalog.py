from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from daily_intel.intelligence.sources.common import USER_AGENT, canonicalize_url, host_of


SECTION_RE = re.compile(r"^##\s+(资源|工具|文摘)\s*$", re.M)
LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
SKIP_HOSTS = {
    "twitter.com", "x.com", "youtube.com", "youtu.be",
    "weibo.com", "zhihu.com", "wikipedia.org",
}


def parse_weekly_markdown(text: str, source_name: str = "") -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    matches = list(SECTION_RE.finditer(text or ""))
    for index, heading in enumerate(matches):
        start = heading.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = heading.group(1)
        body = text[start:end]
        for title, url in LINK_RE.findall(body):
            host = host_of(url)
            if not host or any(host == skip or host.endswith("." + skip) for skip in SKIP_HOSTS):
                continue
            if "github.com/ruanyf/weekly" in url.lower():
                continue
            rows.append({
                "title": title.strip(),
                "url": canonicalize_url(url),
                "section": section,
                "source": source_name,
                "domain": host,
            })
    return rows


def parse_weekly_docs(docs_dir: Path) -> list[dict[str, str]]:
    if not docs_dir.exists():
        return []
    rows: list[dict[str, str]] = []
    for path in sorted(docs_dir.glob("*.md")):
        rows.extend(parse_weekly_markdown(path.read_text(encoding="utf-8", errors="replace"), path.name))
    return rows


def unique_domains(rows: list[dict[str, str]]) -> list[str]:
    seen: list[str] = []
    for row in rows:
        domain = row.get("domain") or host_of(row.get("url") or "")
        if domain and domain not in seen:
            seen.append(domain)
    return seen


def candidate_feed_urls(domain: str) -> list[str]:
    base = f"https://{domain}"
    return [
        f"{base}/feed",
        f"{base}/rss.xml",
        f"{base}/atom.xml",
        f"{base}/index.xml",
        f"{base}/feed.xml",
        f"{base}/rss/",
    ]


def probe_domain_feed(domain: str, timeout: int = 8) -> str:
    from daily_intel.infrastructure.http import http_get

    for url in candidate_feed_urls(domain):
        try:
            response = http_get(
                url,
                timeout=timeout,
                headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml,application/atom+xml,text/xml,*/*"},
                allow_redirects=True,
            )
            if response.status_code >= 400:
                continue
            body = (response.text or "")[:800].lower()
            if "<rss" in body or "<feed" in body or "application/rss" in (response.headers.get("content-type") or ""):
                return canonicalize_url(str(response.url or url))
        except Exception:
            continue
    return ""


def build_blog_feed_configs(rows: list[dict[str, str]], limit: int = 12) -> list[dict[str, Any]]:
    feeds: list[dict[str, Any]] = []
    for domain in unique_domains(rows):
        feed_url = probe_domain_feed(domain)
        if not feed_url:
            continue
        slug = re.sub(r"[^a-z0-9]+", "_", domain.lower()).strip("_")[:40]
        feeds.append({
            "id": f"weekly_pool_{slug}",
            "name": f"周刊外链 {domain}",
            "url": feed_url,
            "tier": 3,
            "lane": "general",
            "publisher_id": "weekly_blog_pool",
            "max_items": 5,
            "fetch_full_text": False,
            "unshorten": True,
        })
        if len(feeds) >= max(1, limit):
            break
    return feeds


def write_blog_feed_pool(path: Path, feeds: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(feeds, ensure_ascii=False, indent=2), encoding="utf-8")
