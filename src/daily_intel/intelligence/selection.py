from __future__ import annotations

import hashlib
import json
from typing import Any

from daily_intel.core.models import Document, Event
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

    def select(
        self, event_docs: list[tuple[Event, list[Document]]], cache_scope: str,
    ) -> tuple[list[tuple[Event, list[Document]]], str | None]:
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
                    return [
                        by_id[event_id] for event_id in payload["event_ids"]
                        if event_id in by_id
                    ], None
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
            selected = self.balance([(event, docs) for _, event, docs in ranked])
            self.repository.set_state(cache_key, json.dumps({
                "signature": signature,
                "event_ids": [event.id for event, _ in selected],
            }))
            return selected, None
        except Exception as exc:
            return self.balance(event_docs), f"scout: {type(exc).__name__}: {exc}"

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
