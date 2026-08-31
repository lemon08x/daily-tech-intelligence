from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from daily_intel.core.models import Document, Event
from daily_intel.core.ports import IntelligenceRepository
from daily_intel.intelligence.clustering import cluster_documents
from daily_intel.intelligence.sources.common import event_lane


Clusterer = Callable[[list[Document], list[dict], int, int], list[Event]]


class EventCatalog:
    """Persists documents and exposes clustered events to any selection strategy."""

    def __init__(
        self, settings: dict, repository: IntelligenceRepository,
        clusterer: Clusterer = cluster_documents,
    ) -> None:
        self.settings = settings
        self.config = settings["intelligence"]
        self.repository = repository
        self.clusterer = clusterer

    def index_and_discover(
        self, documents: list[Document], now: datetime,
    ) -> list[tuple[Event, list[Document]]]:
        for document in documents:
            self.repository.upsert_document(document)
        recent_since = now.astimezone(timezone.utc) - timedelta(
            hours=int(self.config["cluster_window_hours"])
        )
        events = self.clusterer(
            self.repository.recent_documents(recent_since),
            self.settings["topics"],
            int(self.config["cluster_window_hours"]),
            int(self.config["title_similarity_threshold"]),
        )
        pairs = [
            (event, self.repository.get_documents(event.document_ids))
            for event in events
        ]
        selected = take_scout_quota(
            pairs,
            int(self.config.get("max_scout_general", 20)),
            int(self.config.get("max_scout_hardcore", 20)),
        )
        for event, _ in selected:
            self.repository.upsert_event(event)
        return selected


def take_scout_quota(
    event_docs: list[tuple[Event, list[Document]]],
    max_general: int,
    max_hardcore: int,
) -> list[tuple[Event, list[Document]]]:
    """Keep separate scout slots so weeklies are not crowded out by papers."""
    general: list[tuple[Event, list[Document]]] = []
    hardcore: list[tuple[Event, list[Document]]] = []
    for pair in event_docs:
        if event_lane(pair[1]) == "general":
            if len(general) < max(0, max_general):
                general.append(pair)
        elif len(hardcore) < max(0, max_hardcore):
            hardcore.append(pair)
        if len(general) >= max(0, max_general) and len(hardcore) >= max(0, max_hardcore):
            break
    order = {id(item): index for index, item in enumerate(event_docs)}
    return sorted(general + hardcore, key=lambda item: order[id(item)])
