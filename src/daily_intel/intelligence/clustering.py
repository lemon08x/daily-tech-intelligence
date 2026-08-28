from __future__ import annotations

import hashlib
import re
from datetime import timedelta

from rapidfuzz.fuzz import token_set_ratio

from daily_intel.core.models import Document, Event
from daily_intel.intelligence.sources.common import project_identity_keys


IMPACT_WORDS = {
    "release", "launch", "breakthrough", "benchmark", "production", "commercial",
    "deploy", "approval", "investment", "发布", "突破", "量产", "商业化", "部署", "获批",
}
DEPTH_WORDS = {
    "architecture", "algorithm", "dataset", "evaluation", "method", "experiment",
    "framework", "模型", "算法", "数据集", "评测", "架构", "实验", "方法",
}


def is_obvious_build_title(value: str) -> bool:
    title = value.strip().lower()
    return bool(
        re.fullmatch(r"b\d{4,}", title)
        or re.match(r"^(trunk|nightly|viable/strict)/[0-9a-f]{7,}", title)
        or "pinned vllm hash" in title
        or re.match(r"^(deps?|dependencies):?\s+(bump|update)", title)
    )


def _routine_release(document: Document) -> bool:
    """Drop obvious nightly/build automation records before they consume AI slots."""
    return document.content_type == "github_release" and is_obvious_build_title(document.title)


def assign_topic(document: Document, topics: list[dict]) -> tuple[str, str, float]:
    haystack = f"{document.title} {document.summary}".lower()
    best: tuple[str, str, float] = ("", "", 0.0)
    for topic in topics:
        hits = sum(1 for keyword in topic["keywords"] if keyword.lower() in haystack)
        score = min(100.0, hits * 25.0)
        if score > best[2]:
            best = (topic["id"], topic["name"], score)
    return best


def _normalize_title(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", " ", value.lower()).strip()


def cluster_documents(
    documents: list[Document], topics: list[dict], window_hours: int, similarity_threshold: int
) -> list[Event]:
    assigned: list[tuple[Document, str, str, float]] = []
    for document in documents:
        if _routine_release(document):
            continue
        topic_id, topic_name, relevance = assign_topic(document, topics)
        if topic_id:
            assigned.append((document, topic_id, topic_name, relevance))
    assigned.sort(key=lambda item: item[0].published_at, reverse=True)

    groups: list[list[tuple[Document, str, str, float]]] = []
    for item in assigned:
        document, topic_id, _, _ = item
        matched = False
        for group in groups:
            leader, leader_topic, _, _ = group[0]
            if abs(document.published_at - leader.published_at) > timedelta(hours=window_hours):
                continue
            same_project = bool(_project_keys(document) & _project_keys(leader))
            if not same_project:
                if topic_id != leader_topic:
                    continue
                if token_set_ratio(
                    _normalize_title(document.title), _normalize_title(leader.title)
                ) < similarity_threshold:
                    continue
            group.append(item)
            matched = True
            break
        if not matched:
            groups.append([item])

    events: list[Event] = []
    for group in groups:
        docs = [item[0] for item in group]
        topic_id, topic_name = group[0][1], group[0][2]
        source_quality = max(35.0, max(125.0 - doc.source_tier * 25.0 for doc in docs))
        relevance = max(item[3] for item in group)
        text = " ".join(f"{doc.title} {doc.summary}" for doc in docs).lower()
        depth = min(100.0, sum(word in text for word in DEPTH_WORDS) * 20.0)
        impact = min(100.0, sum(word in text for word in IMPACT_WORDS) * 20.0)
        recency = 100.0
        corroboration = min(10.0, (len({doc.source_id for doc in docs}) - 1) * 5.0)
        score = source_quality * .25 + relevance * .25 + recency * .20 + depth * .15 + impact * .15 + corroboration
        doc_ids = sorted(doc.id for doc in docs)
        event_id = hashlib.sha256("\0".join(doc_ids).encode()).hexdigest()[:24]
        events.append(
            Event(
                id=event_id, title=docs[0].title, topic_id=topic_id, topic_name=topic_name,
                document_ids=doc_ids, first_seen=min(doc.published_at for doc in docs),
                last_seen=max(doc.published_at for doc in docs), source_quality=source_quality,
                deterministic_score=min(100.0, score),
            )
        )
    return sorted(events, key=lambda event: (event.deterministic_score, event.last_seen), reverse=True)


def _project_keys(document: Document) -> set[str]:
    keys = project_identity_keys(document.canonical_url or document.url)
    target = str((document.metadata or {}).get("target_url") or "")
    if target:
        keys |= project_identity_keys(target)
    return keys
