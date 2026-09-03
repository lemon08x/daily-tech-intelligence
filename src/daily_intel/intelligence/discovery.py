from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from daily_intel.core.models import Document, Event
from daily_intel.core.ports import IntelligenceRepository
from daily_intel.intelligence.clustering import assign_topic, cluster_documents, is_routine_release


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
        self.last_funnel: dict[str, int] = {}
        self.last_trace: list[dict] = []

    def index_and_discover(
        self, documents: list[Document], now: datetime,
    ) -> list[tuple[Event, list[Document]]]:
        inserted = sum(self.repository.upsert_document(document) for document in documents)
        recent_since = now.astimezone(timezone.utc) - timedelta(
            hours=int(self.config["cluster_window_hours"])
        )
        recent_documents = self.repository.recent_documents(recent_since)
        hard_filtered = [item for item in recent_documents if is_routine_release(item)]
        unclassified = [
            item for item in recent_documents
            if not is_routine_release(item) and not assign_topic(item, self.settings["topics"])[0]
        ]
        events = self.clusterer(
            recent_documents,
            self.settings["topics"],
            int(self.config["cluster_window_hours"]),
            int(self.config["title_similarity_threshold"]),
        )
        pairs = [
            (event, self.repository.get_documents(event.document_ids))
            for event in events
        ]
        for event, _ in pairs:
            self.repository.upsert_event(event)
        self.last_funnel = {
            "collected_documents": len(documents),
            "new_documents": inserted,
            "duplicate_documents": len(documents) - inserted,
            "window_documents": len(recent_documents),
            "hard_filtered_documents": len(hard_filtered),
            "unclassified_documents": len(unclassified),
            "candidate_events": len(pairs),
        }
        self.last_trace = [
            {
                "document_id": item.id,
                "title": item.title,
                "source": item.source_id,
                "status": "hard_filtered",
                "reason": "明显的 CI、nightly 或自动构建 Release",
            }
            for item in hard_filtered
        ]
        return pairs
