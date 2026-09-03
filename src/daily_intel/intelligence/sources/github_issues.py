from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import requests

from daily_intel.core.models import Document
from daily_intel.intelligence.sources.common import (
    USER_AGENT,
    canonicalize_url,
    content_hash,
    document_id,
    document_source_id,
    effective_limit,
    extract_http_urls,
    parse_iso_datetime,
    plain_text,
    source_metadata,
)


def _github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
    }
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or ""
    if token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    return headers


def _rate_limit_error(response: Any) -> str:
    if int(getattr(response, "status_code", 0) or 0) not in {403, 429}:
        return ""
    headers = getattr(response, "headers", {}) or {}
    if str(headers.get("X-RateLimit-Remaining", "")) != "0":
        return ""
    reset = str(headers.get("X-RateLimit-Reset") or "").strip()
    suffix = f"，重置时间戳 {reset}" if reset else ""
    return f"GitHub API 匿名额度已用尽{suffix}；请设置 GITHUB_TOKEN 或 GH_TOKEN"


def _external_target(body: str, issue_url: str, repo: str) -> str:
    repository_prefix = f"https://github.com/{repo}".lower()
    for candidate in extract_http_urls(body):
        canonical = canonicalize_url(candidate)
        if canonical and canonical.lower().startswith(repository_prefix):
            continue
        if canonical and canonical != canonicalize_url(issue_url):
            return canonical
    return ""


class GitHubIssuesSource:
    """Collect each newly created repository Issue through GitHub's REST API."""

    def __init__(self, config: dict[str, Any], timeout: int = 20) -> None:
        self.config = config
        self.source_id = str(config["id"])
        self.timeout = timeout

    def collect(self, since: datetime, limit: int) -> list[Document]:
        repo = str(self.config.get("repo") or "").strip("/")
        source_limit = effective_limit(self.config, limit)
        since_utc = since.astimezone(timezone.utc)
        response = requests.get(
            f"https://api.github.com/repos/{repo}/issues",
            params={
                "state": "all",
                "sort": "created",
                "direction": "desc",
                "since": since_utc.isoformat().replace("+00:00", "Z"),
                "per_page": 100,
            },
            headers=_github_headers(),
            timeout=self.timeout,
        )
        rate_limit_error = _rate_limit_error(response)
        if rate_limit_error:
            raise RuntimeError(rate_limit_error)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("GitHub Issues API 没有返回列表")

        now = datetime.now(timezone.utc)
        document_source = document_source_id(self.config)
        documents: list[Document] = []
        for item in payload:
            if not isinstance(item, dict) or item.get("pull_request"):
                continue
            created = parse_iso_datetime(str(item.get("created_at") or ""))
            if created is None or created < since_utc:
                continue
            number = int(item.get("number") or 0)
            title = plain_text(str(item.get("title") or ""))
            url = str(item.get("html_url") or "")
            if number <= 0 or not title or not url:
                continue
            body = plain_text(str(item.get("body") or ""))
            labels = [
                plain_text(str(label.get("name") or ""))
                for label in (item.get("labels") or [])
                if isinstance(label, dict) and label.get("name")
            ]
            user = item.get("user") if isinstance(item.get("user"), dict) else {}
            external_id = f"{repo}#{number}"
            metadata = {
                **source_metadata(self.config),
                "api_url": f"https://api.github.com/repos/{repo}/issues",
                "repository": repo,
                "issue_number": number,
                "issue_state": str(item.get("state") or ""),
                "author": str(user.get("login") or ""),
                "labels": labels,
                "comments": int(item.get("comments") or 0),
            }
            target_url = _external_target(body, url, repo)
            if target_url:
                metadata["target_url"] = target_url
            content = body or title
            documents.append(
                Document(
                    id=document_id(document_source, external_id),
                    source_id=document_source,
                    source_name=str(self.config["name"]),
                    external_id=external_id,
                    title=title,
                    url=url,
                    canonical_url=canonicalize_url(url),
                    published_at=created,
                    fetched_at=now,
                    summary=body or title,
                    content=content,
                    content_hash=content_hash(title, f"{content}\n{external_id}"),
                    source_tier=int(self.config["tier"]),
                    content_type=str(self.config.get("content_type") or "weekly_issue"),
                    extraction_quality="summary",
                    metadata=metadata,
                )
            )
            if len(documents) >= source_limit:
                break
        return documents
