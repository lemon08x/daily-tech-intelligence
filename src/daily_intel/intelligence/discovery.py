from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from daily_intel.core.models import Document, Event
from daily_intel.core.ports import IntelligenceRepository
from daily_intel.intelligence.clustering import cluster_documents


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
        )[: int(self.config["max_scout_events"])]
        for event in events:
            self.repository.upsert_event(event)
        return [
            (event, self.repository.get_documents(event.document_ids))
            for event in events
        ]
