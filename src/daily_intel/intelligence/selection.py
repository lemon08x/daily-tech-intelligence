from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from daily_intel.core.models import Analysis, Document, Event
from daily_intel.core.ports import IntelligenceRepository
from daily_intel.intelligence.modeling import ModelStageRunner
from daily_intel.intelligence.clustering import UNCLASSIFIED_TOPIC_ID, UNCLASSIFIED_TOPIC_NAME
from daily_intel.intelligence.sources.common import event_lane


class EventSelector:
    """Rank-fuses deterministic signals with bounded model judgement."""

    VERSION = "rank-fusion-v3"

    def __init__(
        self, settings: dict[str, Any], repository: IntelligenceRepository,
        stages: ModelStageRunner,
    ) -> None:
        self.settings = settings
        self.config = settings["intelligence"]
        self.repository = repository
        self.stages = stages
        self.deterministic_weight = float(self.config.get("selection_deterministic_weight", .35))
        self.model_weight = float(self.config.get("selection_model_weight", .65))
        self.model_reject_floor = float(self.config.get("selection_model_reject_floor", 55))
        self.repeat_penalty = float(self.config.get("selection_repeat_penalty", .4))
        self.repeat_hours = float(self.config.get("selection_repeat_hours", 36))
        self.topic_names = {
            str(item["id"]): str(item["name"])
            for item in settings.get("topics", [])
        }
        self.topic_names[UNCLASSIFIED_TOPIC_ID] = UNCLASSIFIED_TOPIC_NAME
        self.last_trace: list[dict[str, Any]] = []
        self.last_funnel: dict[str, int] = {}
        self.priority_event_ids: set[str] = set()

    def begin_run(self) -> None:
        self.last_trace = []
        self.last_funnel = {}
        self.priority_event_ids = set()

    def select(
        self, event_docs: list[tuple[Event, list[Document]]], cache_scope: str,
        now: datetime | None = None,
    ) -> tuple[list[tuple[Event, list[Document]]], str | None]:
        current = now or datetime.now(timezone.utc)
        recent = self._recent_analyses(current)
        signature = hashlib.sha256(json.dumps(
            {
                "events": [event.id for event, _ in event_docs],
                "version": self.VERSION,
                "deterministic_weight": self.deterministic_weight,
                "model_weight": self.model_weight,
                "scout_doc_chars": int(self.config.get("scout_doc_chars", 4000)),
            },
            separators=(",", ":"), sort_keys=True,
        ).encode()).hexdigest()
        cache_key = (
            f"scout_selection:{self.stages.prompt_version}:"
            f"{self.VERSION}:{cache_scope}"
        )
        cached = self.repository.get_state(cache_key)
        if cached:
            try:
                payload = json.loads(cached)
                if payload.get("signature") == signature:
                    by_id = {event.id: (event, docs) for event, docs in event_docs}
                    ranked = []
                    for item in payload.get("ranked") or [
                        {"event_id": event_id, "score": None}
                        for event_id in payload.get("event_ids", [])
                    ]:
                        pair = by_id.get(item["event_id"])
                        if pair is None:
                            continue
                        event, docs = pair
                        event = self._with_topic(event, item.get("topic_id"))
                        score = item.get("score")
                        if score is None:
                            score = event.deterministic_score
                        ranked.append((float(score), event, docs))
                    self.last_trace = list(payload.get("trace") or [])
                    self.last_funnel = dict(payload.get("funnel") or {})
                    self.priority_event_ids = {
                        str(item.get("event_id")) for item in self.last_trace
                        if item.get("official_release_priority")
                    }
                    return self._apply_repeat(ranked, recent), None
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                pass
        try:
            response = self.stages.scout(event_docs, self.settings["topics"])
            by_id = {item.event_id: item for item in response.items}
            ranked: list[tuple[float, Event, list[Document]]] = []
            trace: list[dict[str, Any]] = []
            rejected = 0
            fallback = 0
            priority_candidates: set[str] = set()
            for event, docs in event_docs:
                item = by_id.get(event.id)
                if item is None:
                    fallback += 1
                    ranked.append((event.deterministic_score, event, docs))
                    trace.append(self._trace_item(
                        event, docs, "candidate",
                        "Scout 未返回该事件，保留并使用确定性分数排序",
                        event.deterministic_score,
                    ))
                    continue
                event = self._with_topic(event, item.topic_id)
                self.repository.upsert_event(event)
                if not item.relevant and event.deterministic_score < self.model_reject_floor:
                    rejected += 1
                    trace.append(self._trace_item(
                        event, docs, "scout_rejected", item.reason,
                        event.deterministic_score,
                    ))
                    continue
                model_score = (
                    item.relevance * .30
                    + item.novelty * .25
                    + item.technical_depth * .25
                    + item.industry_impact * .20
                )
                score = (
                    event.deterministic_score * self.deterministic_weight
                    + model_score * self.model_weight
                )
                ranked.append((score, event, docs))
                if self._is_official_release(event, docs, item.industry_impact, item.relevant):
                    priority_candidates.add(event.id)
                traced = self._trace_item(event, docs, "candidate", item.reason, score)
                traced.update({
                    "deterministic_score": round(float(event.deterministic_score), 2),
                    "model_score": round(float(model_score), 2),
                    "model_relevant": bool(item.relevant),
                })
                trace.append(traced)
            ranked.sort(key=lambda item: (item[0], item[1].last_seen), reverse=True)
            priority_limit = max(
                0, int(self.config.get("preferred_official_release_events", 3))
            )
            priority_ranked = [
                event.id for _, event, _ in ranked
                if event.id in priority_candidates
            ]
            self.priority_event_ids = set(priority_ranked[:priority_limit])
            for traced in trace:
                traced["official_release_priority"] = (
                    traced.get("event_id") in self.priority_event_ids
                )
            funnel = {
                "scout_input_events": len(event_docs),
                "scout_reviewed_events": len(by_id),
                "scout_fallback_events": fallback,
                "scout_rejected_events": rejected,
                "scout_retained_events": len(ranked),
                "official_release_priority_events": len(self.priority_event_ids),
            }
            self.last_trace = trace
            self.last_funnel = funnel
            self.repository.set_state(cache_key, json.dumps({
                "signature": signature,
                "event_ids": [event.id for _, event, _ in ranked],
                "ranked": [
                    {
                        "event_id": event.id,
                        "score": score,
                        "topic_id": event.topic_id,
                    }
                    for score, event, _ in ranked
                ],
                "trace": trace,
                "funnel": funnel,
            }))
            return self._apply_repeat(ranked, recent), None
        except Exception as exc:
            self.last_trace = [
                self._trace_item(
                    event, docs, "candidate",
                    f"Scout 失败，使用确定性排序：{type(exc).__name__}",
                    event.deterministic_score,
                )
                for event, docs in event_docs
            ]
            self.last_funnel = {
                "scout_input_events": len(event_docs),
                "scout_reviewed_events": 0,
                "scout_fallback_events": len(event_docs),
                "scout_rejected_events": 0,
                "scout_retained_events": len(event_docs),
                "official_release_priority_events": 0,
            }
            return self.order_with_repeat(event_docs, current), f"scout: {type(exc).__name__}: {exc}"

    def order_with_repeat(
        self, event_docs: list[tuple[Event, list[Document]]], now: datetime,
    ) -> list[tuple[Event, list[Document]]]:
        recent = self._recent_analyses(now)
        scored: list[tuple[float, Event, list[Document]]] = []
        for event, docs in event_docs:
            factor = self._repeat_factor(event, docs, recent)
            scored.append((event.deterministic_score * factor, event, docs))
        scored.sort(key=lambda item: (item[0], item[1].last_seen), reverse=True)
        if not self.last_trace:
            self.last_trace = [
                self._trace_item(
                    event, docs, "candidate", "AI 未启用，使用确定性排序", score,
                )
                for score, event, docs in scored
            ]
            self.last_funnel = {
                "scout_input_events": len(event_docs),
                "scout_reviewed_events": 0,
                "scout_fallback_events": len(event_docs),
                "scout_rejected_events": 0,
                "scout_retained_events": len(event_docs),
                "official_release_priority_events": 0,
            }
        return [(event, docs) for _, event, docs in scored]

    def _apply_repeat(
        self,
        ranked: list[tuple[float, Event, list[Document]]],
        recent: list[Analysis],
    ) -> list[tuple[Event, list[Document]]]:
        adjusted = [
            (score * self._repeat_factor(event, docs, recent), event, docs)
            for score, event, docs in ranked
        ]
        adjusted.sort(key=lambda item: (item[0], item[1].last_seen), reverse=True)
        return [(event, docs) for _, event, docs in adjusted]

    def _with_topic(self, event: Event, topic_id: str | None) -> Event:
        resolved = str(topic_id or "")
        if resolved not in self.topic_names:
            resolved = event.topic_id if event.topic_id in self.topic_names else UNCLASSIFIED_TOPIC_ID
        return event.model_copy(update={
            "topic_id": resolved,
            "topic_name": self.topic_names[resolved],
        })

    @staticmethod
    def _is_official_release(
        event: Event, docs: list[Document], industry_impact: float, relevant: bool,
    ) -> bool:
        if not relevant or industry_impact < 65:
            return False
        official_article = any(
            doc.source_tier == 1
            and doc.content_type == "article"
            and str((doc.metadata or {}).get("publisher_id") or doc.source_id) != "arxiv"
            for doc in docs
        )
        if not official_article:
            return False
        return bool(re.search(
            r"\b(?:introducing|launch(?:ed)?|release[ds]?|announc(?:e|ed|es|ing)|unveil(?:ed|s|ing))\b|发布|推出|释出",
            event.title,
            re.IGNORECASE,
        ))

    @staticmethod
    def _trace_item(
        event: Event, docs: list[Document], status: str, reason: str, score: float,
    ) -> dict[str, Any]:
        return {
            "event_id": event.id,
            "title": event.title,
            "topic_id": event.topic_id,
            "lane": event_lane(docs),
            "status": status,
            "score": round(float(score), 2),
            "reason": str(reason or "")[:500],
        }

    def _recent_analyses(self, now: datetime) -> list[Analysis]:
        current = _aware(now)
        start_of_today = current.replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff = start_of_today - timedelta(hours=self.repeat_hours)
        return [
            item for item in self.repository.get_latest_analyses(80)
            if cutoff <= _aware(item.created_at) < start_of_today
        ]

    def _repeat_factor(
        self, event: Event, docs: list[Document], recent: list[Analysis],
    ) -> float:
        if not recent or self.repeat_penalty >= 1:
            return 1.0
        urls = {str(doc.canonical_url or doc.url).rstrip("/") for doc in docs if doc.url}
        previous: Analysis | None = None
        for item in recent:
            item_urls = {str(ev.url).rstrip("/") for ev in item.evidence if ev.url}
            if item.event_id == event.id or (urls and urls & item_urls):
                previous = item
                break
        if previous is None:
            return 1.0
        if _aware(event.last_seen) > _aware(previous.created_at) + timedelta(hours=2):
            return min(1.0, self.repeat_penalty + 0.35)
        return self.repeat_penalty

    @staticmethod
    def prioritize_for_research(
        event_docs: list[tuple[Event, list[Document]]],
        preferred_general: int,
        preferred_hardcore: int,
        preferred_max_per_topic: int,
        priority_event_ids: set[str] | None = None,
    ) -> list[tuple[Event, list[Document]]]:
        """Apply lane/topic preferences, then backfill instead of dropping candidates."""
        preferred_by_lane = {
            "general": max(0, preferred_general),
            "hardcore": max(0, preferred_hardcore),
        }
        topic_cap = max(1, preferred_max_per_topic)
        priority_ids = priority_event_ids or set()
        priority_pairs = [pair for pair in event_docs if pair[0].id in priority_ids]
        regular_pairs = [pair for pair in event_docs if pair[0].id not in priority_ids]
        first_by_lane_topic: list[tuple[Event, list[Document]]] = []
        remainder: list[tuple[Event, list[Document]]] = []
        seen_lane_topics: set[tuple[str, str]] = set()
        for pair in regular_pairs:
            key = (event_lane(pair[1]), pair[0].topic_id)
            if key in seen_lane_topics:
                remainder.append(pair)
            else:
                seen_lane_topics.add(key)
                first_by_lane_topic.append(pair)
        coverage_order = first_by_lane_topic + remainder
        chosen: list[tuple[Event, list[Document]]] = list(priority_pairs)
        deferred_lane: list[tuple[Event, list[Document]]] = []
        deferred_topic: list[tuple[Event, list[Document]]] = []
        lane_counts = {"general": 0, "hardcore": 0}
        topic_counts: dict[str, int] = {}
        for event, docs in priority_pairs:
            lane = event_lane(docs)
            lane_counts[lane] += 1
            topic_counts[event.topic_id] = topic_counts.get(event.topic_id, 0) + 1

        for pair in coverage_order:
            event, docs = pair
            lane = event_lane(docs)
            if topic_counts.get(event.topic_id, 0) >= topic_cap:
                deferred_topic.append(pair)
            elif lane_counts[lane] >= preferred_by_lane[lane]:
                deferred_lane.append(pair)
            else:
                chosen.append(pair)
                lane_counts[lane] += 1
                topic_counts[event.topic_id] = topic_counts.get(event.topic_id, 0) + 1

        for pair in deferred_lane:
            event, docs = pair
            if topic_counts.get(event.topic_id, 0) >= topic_cap:
                deferred_topic.append(pair)
                continue
            chosen.append(pair)
            lane = event_lane(docs)
            lane_counts[lane] += 1
            topic_counts[event.topic_id] = topic_counts.get(event.topic_id, 0) + 1

        return chosen + deferred_topic


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
