from datetime import datetime, timedelta, timezone

from daily_intel.core.models import Document, Event, PublicationRecord
from daily_intel.infrastructure.storage.sqlite import SQLiteIntelligenceRepository
from daily_intel.intelligence.discovery import EventCatalog
from daily_intel.intelligence.selection import EventSelector
from daily_intel.intelligence.snapshots import content_fingerprint, stage_documents


NOW = datetime(2026, 9, 13, 17, tzinfo=timezone.utc)  # Sep 14, 01:00 Shanghai


def setup_case(tmp_path):
    repo = SQLiteIntelligenceRepository(tmp_path / "intel.db")
    doc = Document(
        id="doc", source_id="infoq", source_name="InfoQ", external_id="doc",
        title="OpenAI Releases GPT-6 Astra", url="https://example.com/astra",
        canonical_url="https://example.com/astra", source_tier=2,
        published_at=NOW - timedelta(hours=71), fetched_at=NOW,
        content="Launch details " * 200, content_hash="a" * 64,
    )
    event = Event(id="event", title=doc.title, topic_id="ai", topic_name="AI",
                  document_ids=[doc.id], first_seen=doc.published_at,
                  last_seen=doc.published_at, deterministic_score=90, source_quality=80)
    selector = EventSelector({"intelligence": {}}, repo, None)
    selector.begin_run(experiment_id="production", is_production=True)
    return repo, selector, event, doc


def publish(repo, event, doc, **overrides):
    values = dict(run_id=1, event_id=event.id, canonical_urls=[str(doc.url)],
                  published_at=NOW - timedelta(days=3), is_production=True,
                  experiment_id="production", content_fingerprint=content_fingerprint([doc]))
    values.update(overrides)
    repo.record_publications([PublicationRecord(**values)])


def test_unchanged_old_news_is_excluded_beyond_repeat_window(tmp_path):
    repo, selector, event, doc = setup_case(tmp_path)
    publish(repo, event, doc)
    assert selector.order_with_repeat([(event, [doc])], NOW) == []
    assert selector.last_exposure[event.id]["status"] == "repeat_excluded"
    assert selector.last_exposure[event.id]["freshness_label"] != "首次收录"
    assert selector.last_trace[0]["status"] == "repeat_excluded"


def test_legacy_clipping_and_tracking_url_do_not_create_update(tmp_path):
    repo, selector, event, doc = setup_case(tmp_path)
    publish(repo, event, doc, content_fingerprint=content_fingerprint(stage_documents([doc], 1800)),
            canonical_urls=[str(doc.url) + "?utm_source=feed"])
    renamed = event.model_copy(update={"id": "reclustered"})
    assert selector.order_with_repeat([(renamed, [doc])], NOW) == []


def test_changed_material_and_different_astra_event_remain_candidates(tmp_path):
    repo, selector, event, doc = setup_case(tmp_path)
    publish(repo, event, doc)
    updated = doc.model_copy(update={"content": "New benchmark results and deployment details"})
    assert selector.order_with_repeat([(event, [updated])], NOW)
    assert selector.last_exposure[event.id]["freshness_label"] == "材料有变化，待核对"
    other = event.model_copy(update={"id": "perplexity"})
    other_doc = doc.model_copy(update={"url": "https://example.com/perplexity-astra",
                                       "canonical_url": "https://example.com/perplexity-astra"})
    assert selector.order_with_repeat([(other, [other_doc])], NOW)
    assert selector.last_exposure[other.id]["freshness_label"] == "首次收录"


def test_same_day_rerun_and_experiment_scope_are_preserved(tmp_path):
    repo, selector, event, doc = setup_case(tmp_path)
    publish(repo, event, doc, published_at=NOW - timedelta(minutes=30))
    assert selector.order_with_repeat([(event, [doc])], NOW)
    publish(repo, event, doc, run_id=2, is_production=False, experiment_id="qwen")
    assert selector.order_with_repeat([(event, [doc])], NOW)
    selector.begin_run(experiment_id="qwen", is_production=False)
    assert selector.order_with_repeat([(event, [doc])], NOW) == []


def test_future_documents_wait_until_source_date_including_cached_documents(tmp_path):
    repo, selector, event, doc = setup_case(tmp_path)
    future = doc.model_copy(update={"published_at": NOW + timedelta(hours=7)})
    seen = []
    def clusterer(documents, *_):
        seen.append(documents)
        return []
    catalog = EventCatalog({"intelligence": {"cluster_window_hours": 72,
                           "title_similarity_threshold": 80}, "topics": []}, repo, clusterer)
    catalog.index_and_discover([future], NOW)
    assert seen[-1] == []
    assert catalog.last_funnel["future_dated_documents"] == 1
    assert catalog.last_trace[0]["status"] == "future_dated"
    catalog.index_and_discover([], NOW)
    assert seen[-1] == []
    assert selector.order_with_repeat([(event, [future])], NOW) == []
    catalog.index_and_discover([], NOW + timedelta(hours=7))
    assert seen[-1][0].published_at == future.published_at
