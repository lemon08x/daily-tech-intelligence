from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from daily_intel.core.models import Analysis, Document, Event
from daily_intel.core.ports import IntelligenceRepository
from daily_intel.intelligence.modeling import ModelStageRunner


class EventSelector:
    """Rank-fuses deterministic signals with bounded model judgement."""

    VERSION = "rank-fusion-v1"

    def __init__(
        self, settings: dict[str, Any], repository: IntelligenceRepository,
        stages: ModelStageRunner,
    ) -> None:
        self.settings = settings
        self.config = settings["intelligence"]
        self.repository = repository
        self.stages = stages
        self.deterministic_weight = float(self.config.get("selection_deterministic_weight", .65))
        self.model_weight = float(self.config.get("selection_model_weight", .35))
        self.model_reject_floor = float(self.config.get("selection_model_reject_floor", 55))
        self.repeat_penalty = float(self.config.get("selection_repeat_penalty", .4))
        self.repeat_hours = float(self.config.get("selection_repeat_hours", 36))

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
                        score = item.get("score")
                        if score is None:
                            score = event.deterministic_score
                        ranked.append((float(score), event, docs))
                    return self._apply_repeat(ranked, recent), None
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                pass
        try:
            response = self.stages.scout(event_docs, self.settings["topics"])
            by_id = {item.event_id: item for item in response.items}
            ranked: list[tuple[float, Event, list[Document]]] = []
            for event, docs in event_docs:
                item = by_id.get(event.id)
                if item is None:
                    ranked.append((event.deterministic_score, event, docs))
                    continue
                if not item.relevant and event.deterministic_score < self.model_reject_floor:
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
            ranked.sort(key=lambda item: (item[0], item[1].last_seen), reverse=True)
            self.repository.set_state(cache_key, json.dumps({
                "signature": signature,
                "event_ids": [event.id for _, event, _ in ranked],
                "ranked": [
                    {"event_id": event.id, "score": score}
                    for score, event, _ in ranked
                ],
            }))
            return self._apply_repeat(ranked, recent), None
        except Exception as exc:
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
        return self.balance([(event, docs) for _, event, docs in scored])

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
        return self.balance([(event, docs) for _, event, docs in adjusted])

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
    def balance(
        event_docs: list[tuple[Event, list[Document]]],
    ) -> list[tuple[Event, list[Document]]]:
        first_by_topic: list[tuple[Event, list[Document]]] = []
        remainder: list[tuple[Event, list[Document]]] = []
        seen: set[str] = set()
        for item in event_docs:
            topic = item[0].topic_id
            if topic not in seen:
                seen.add(topic)
                first_by_topic.append(item)
            else:
                remainder.append(item)
        return first_by_topic + remainder


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
