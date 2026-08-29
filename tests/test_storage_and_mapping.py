from __future__ import annotations

from datetime import datetime, timedelta, timezone

from daily_intel.core.models import (
    Analysis,
    AnalysisQuality,
    AnalysisStatus,
    Document,
    Event,
)
from daily_intel.infrastructure.storage.sqlite import SQLiteIntelligenceRepository


def _doc() -> Document:
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    return Document(
        id="doc-1", source_id="source", source_name="Source", external_id="external",
        title="AI accelerator", url="https://example.com/a", canonical_url="https://example.com/a",
        published_at=now, fetched_at=now, summary="A sufficiently long source summary.",
        content="A sufficiently long source summary.", content_hash="a" * 64,
        source_tier=1, metadata={"source_name": "Source"},
    )


def test_sqlite_document_idempotency_and_state(tmp_path) -> None:
    repository = SQLiteIntelligenceRepository(tmp_path / "intelligence.db")
    assert repository.upsert_document(_doc()) is True
    assert repository.upsert_document(_doc()) is False
    assert len(repository.recent_documents(datetime(2026, 8, 23, tzinfo=timezone.utc))) == 1
    repository.set_state("cursor", "value")
    assert repository.get_state("cursor") == "value"


def test_sqlite_keeps_model_scoped_analysis_variants(tmp_path) -> None:
    repository = SQLiteIntelligenceRepository(tmp_path / "intelligence.db")
    document = _doc()
    repository.upsert_document(document)
    event = Event(
        id="event-1", title=document.title, topic_id="compute", topic_name="芯片算力",
        document_ids=[document.id], first_seen=document.published_at,
        last_seen=document.published_at, source_quality=100,
        deterministic_score=90,
    )
    repository.upsert_event(event)
    first = Analysis(
        event_id=event.id, status=AnalysisStatus.LEAD, headline="Model A",
        confidence=.3, model="model-a", prompt_version="v2",
        quality=AnalysisQuality(policy_version="evidence-gate-v1"),
        created_at=document.published_at,
    )
    second = first.model_copy(update={
        "headline": "Model B", "model": "model-b",
        "created_at": document.published_at + timedelta(minutes=1),
    })
    repository.save_analysis(first, "experiment-a")
    repository.save_analysis(second, "experiment-b")
    assert repository.get_analysis(event.id, "experiment-a").model == "model-a"
    assert repository.get_analysis(event.id, "experiment-b").model == "model-b"
    latest = repository.get_latest_analyses(5)
    assert len(latest) == 1
    assert latest[0].model == "model-b"
