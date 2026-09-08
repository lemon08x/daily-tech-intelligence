from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from daily_intel.core.models import (
    Analysis,
    AnalysisQuality,
    AnalysisStatus,
    Document,
    Event,
    PublicationRecord,
)
from daily_intel.intelligence.snapshots import content_fingerprint
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


def test_sqlite_skips_unparseable_legacy_analysis_rows(tmp_path) -> None:
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
    repository.save_analysis(
        Analysis(
            event_id=event.id, status=AnalysisStatus.LEAD, headline="Model A",
            confidence=.3, model="model-a", prompt_version="v2",
            quality=AnalysisQuality(policy_version="evidence-gate-v1"),
            created_at=document.published_at,
        ),
        "experiment-a",
    )
    import sqlite3

    with sqlite3.connect(tmp_path / "intelligence.db") as connection:
        connection.execute(
            "INSERT INTO analysis_variants VALUES(?,?,?,?,?,?,?)",
            (
                "legacy-event", "scope", "deep", "old", "v0",
                json.dumps({"event_id": "legacy-event", "status": "deep"}),
                document.published_at.isoformat(),
            ),
        )
    assert repository.get_analysis("legacy-event", "scope") is None
    assert repository.get_analysis(event.id, "experiment-a") is not None
    assert [item.event_id for item in repository.get_latest_analyses(10)] == [event.id]


def test_r10_ledger_rebuild_is_idempotent_and_ignores_failed_runs(tmp_path) -> None:
    repository = SQLiteIntelligenceRepository(tmp_path / "intelligence.db")
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    success_id = repository.start_run({"experiment_id": "deepseek-v4-flash"})
    repository.finish_run(success_id, "success", {
        "experiment_id": "deepseek-v4-flash",
        "generated_at": now.isoformat(),
        "intelligence": {
            "processing_trace": [
                {"event_id": "evt-a", "status": "published", "selection_order": 1,
                 "publication_tier": "extensive"},
            ]
        },
    })
    failed_id = repository.start_run({"experiment_id": "deepseek-v4-flash"})
    repository.finish_run(failed_id, "failed", {
        "experiment_id": "deepseek-v4-flash",
        "intelligence": {
            "processing_trace": [
                {"event_id": "evt-b", "status": "published", "selection_order": 1,
                 "publication_tier": "intensive"},
            ]
        },
    })
    inserted = repository.rebuild_publication_ledger()
    again = repository.rebuild_publication_ledger()
    recent = repository.recent_publications(
        datetime(2020, 1, 1, tzinfo=timezone.utc),
        datetime(2030, 1, 1, tzinfo=timezone.utc),
        production_only=True,
    )
    assert inserted >= 1
    assert again == 0
    assert {item.event_id for item in recent} == {"evt-a"}
    assert all(item.research_status == "unknown" for item in recent)


def test_r9_whitespace_refetch_is_not_new_material(tmp_path) -> None:
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    first = _doc()
    second = first.model_copy(update={
        "content": "A   sufficiently   long source summary.",
        "summary": "A   sufficiently   long source summary.",
        "fetched_at": now + timedelta(hours=3),
    })
    assert content_fingerprint([first]) == content_fingerprint([second])
    changed = first.model_copy(update={"content": "A different technical body with new numbers 12.6."})
    assert content_fingerprint([first]) != content_fingerprint([changed])
    repository = SQLiteIntelligenceRepository(tmp_path / "intel.db")
    repository.record_publications([
        PublicationRecord(
            run_id=1, event_id="prod-event", canonical_urls=["https://example.com/a"],
            experiment_id="qwen3.8-27b", is_production=False,
            selection_order=1, publication_tier="extensive",
            research_status="broad_only", published_at=now, content_fingerprint="x",
        )
    ])
    production = repository.recent_publications(
        now - timedelta(days=1), now + timedelta(days=1), production_only=True,
    )
    assert production == []
