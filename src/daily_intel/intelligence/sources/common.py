from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from time import struct_time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


USER_AGENT = "DailyIntel/0.4 (+local research digest)"


def as_datetime(value: struct_time | None, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    return datetime(*value[:6], tzinfo=timezone.utc)


def parse_iso_datetime(value: str | None, fallback: datetime | None = None) -> datetime | None:
    if not value:
        return fallback
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def plain_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", html.unescape(value or ""))
    return re.sub(r"\s+", " ", value).strip()


SHORTENER_HOSTS = {
    "t.co", "bit.ly", "tinyurl.com", "j.mp", "ow.ly", "buff.ly", "lnkd.in",
    "trib.al", "cutt.ly", "rb.gy", "shorturl.at", "tiny.cc",
}
HARDCORE_CONTENT_TYPES = {"paper", "github_release"}
HTTP_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")]
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), "")
    )


def host_of(url: str) -> str:
    return urlsplit(url).netloc.lower().lstrip("www.")


def is_short_url(url: str) -> bool:
    host = host_of(url)
    return host in SHORTENER_HOSTS or host.endswith(".link") or "/link/" in urlsplit(url).path.lower()


def should_unshorten(url: str, config: dict | None = None) -> bool:
    if config and config.get("unshorten"):
        return True
    return is_short_url(url)


def extract_http_urls(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for raw in HTTP_URL_RE.findall(text or ""):
        cleaned = raw.rstrip(".,;:)]}>\"'")
        key = canonicalize_url(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        found.append(cleaned)
    return found


def document_lane(config: dict, content_type: str = "article") -> str:
    explicit = str(config.get("lane") or "").strip()
    if explicit in {"general", "hardcore"}:
        return explicit
    if content_type in HARDCORE_CONTENT_TYPES:
        return "hardcore"
    if int(config.get("tier", 1)) >= 2:
        return "general"
    return "hardcore"


def event_lane(documents: list) -> str:
    if not documents:
        return "hardcore"
    if any(
        getattr(doc, "content_type", "") in HARDCORE_CONTENT_TYPES
        or str((getattr(doc, "metadata", {}) or {}).get("lane") or "") == "hardcore"
        for doc in documents
    ):
        return "hardcore"
    if all(
        str((getattr(doc, "metadata", {}) or {}).get("lane") or "") == "general"
        or int(getattr(doc, "source_tier", 1)) >= 2
        for doc in documents
    ):
        return "general"
    return "hardcore"


def project_identity_keys(url: str) -> set[str]:
    canonical = canonicalize_url(url)
    if not canonical:
        return set()
    parts = urlsplit(canonical)
    host = parts.netloc.lower()
    path = parts.path.strip("/")
    keys = {canonical}
    if "github.com" in host:
        segments = path.split("/")
        if len(segments) >= 2 and segments[0] not in {"login", "topics", "orgs", "settings"}:
            keys.add(f"github:{segments[0]}/{segments[1]}".lower())
    return keys


def resolve_public_url(url: str, timeout: int = 12) -> str:
    from daily_intel.infrastructure.http import http_get

    try:
        response = http_get(
            url,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
            allow_redirects=True,
        )
        final = str(getattr(response, "url", "") or url)
        return canonicalize_url(final)
    except Exception:
        return canonicalize_url(url)


def document_id(source_id: str, external_id: str) -> str:
    return hashlib.sha256(f"{source_id}\0{external_id}".encode()).hexdigest()[:24]


def content_hash(title: str, content: str) -> str:
    normalized = re.sub(r"\W+", "", f"{title}{content}").lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def document_source_id(config: dict) -> str:
    """Return the evidence publisher, which may differ from the discovery adapter."""
    return str(config.get("publisher_id") or config["id"])


def effective_limit(config: dict, requested: int) -> int:
    configured = int(config.get("max_items", requested))
    return max(1, min(requested, configured))


def _contains_keyword(text: str, keyword: str) -> bool:
    keyword = keyword.casefold().strip()
    if not keyword:
        return False
    if len(keyword) <= 3 and keyword.isalnum():
        return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text) is not None
    return keyword in text


def passes_keyword_filters(title: str, summary: str, config: dict) -> bool:
    haystack = f"{title} {summary}".casefold()
    includes = [str(item) for item in config.get("include_keywords", [])]
    excludes = [str(item) for item in config.get("exclude_keywords", [])]
    if includes and not any(_contains_keyword(haystack, item) for item in includes):
        return False
    return not any(_contains_keyword(haystack, item) for item in excludes)


def source_metadata(config: dict) -> dict[str, str]:
    content_type = str(config.get("content_type") or "article")
    metadata = {
        "source_name": str(config["name"]),
        "collector_id": str(config["id"]),
        "publisher_id": document_source_id(config),
        "lane": document_lane(config, content_type),
    }
    if config.get("topic_hint"):
        metadata["topic_hint"] = str(config["topic_hint"])
    return metadata
