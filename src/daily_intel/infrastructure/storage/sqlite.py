from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from pydantic import ValidationError

from daily_intel.core.models import Analysis, Document, Event, PublicationRecord


SCHEMA_VERSION = 5


class SQLiteIntelligenceRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    canonical_url TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL UNIQUE,
                    source_tier INTEGER NOT NULL,
                    content_type TEXT NOT NULL,
                    extraction_quality TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    UNIQUE(source_id, external_id)
                );
                CREATE INDEX IF NOT EXISTS idx_documents_published ON documents(published_at);

                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    topic_id TEXT NOT NULL,
                    topic_name TEXT NOT NULL,
                    document_ids_json TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    source_quality REAL NOT NULL,
                    deterministic_score REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_last_seen ON events(last_seen);

                CREATE TABLE IF NOT EXISTS analysis_variants (
                    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    cache_scope TEXT NOT NULL,
                    status TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    analysis_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(event_id, cache_scope)
                );
                CREATE INDEX IF NOT EXISTS idx_analysis_variants_created
                    ON analysis_variants(created_at);
                CREATE TABLE IF NOT EXISTS evidence (
                    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    document_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    quote TEXT NOT NULL,
                    locator TEXT NOT NULL,
                    PRIMARY KEY(event_id, position)
                );
                CREATE TABLE IF NOT EXISTS llm_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stage TEXT NOT NULL,
                    event_id TEXT,
                    model TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pipeline_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            self._migrate(db)

    def upsert_document(self, document: Document) -> bool:
        payload = document.model_dump(mode="json")
        with self._connect() as db:
            existing = db.execute(
                "SELECT id, source_id, external_id, metadata_json FROM documents WHERE content_hash=? OR (source_id=? AND external_id=?)",
                (document.content_hash, document.source_id, document.external_id),
            ).fetchone()
            if existing:
                # Repair a legacy sitemap lastmod date only from explicit
                # publication metadata for the same source document. Research
                # snapshots and historical publication records remain immutable.
                if (existing["source_id"] == document.source_id
                        and existing["external_id"] == document.external_id
                        and document.metadata.get("sitemap_url")
                        and document.metadata.get("date_basis") == "published"
                        and document.metadata.get("original_published_at")):
                    metadata = json.loads(existing["metadata_json"])
                    metadata.update({key: document.metadata[key] for key in (
                        "original_published_at", "source_updated_at", "date_basis"
                    ) if key in document.metadata})
                    db.execute("UPDATE documents SET published_at=?, metadata_json=? WHERE id=?",
                               (payload["published_at"], json.dumps(metadata, ensure_ascii=False), existing["id"]))
                return False
            db.execute(
                """INSERT INTO documents(
                    id, source_id, external_id, title, url, canonical_url, published_at,
                    fetched_at, summary, content, content_hash, source_tier, content_type,
                    extraction_quality, metadata_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    document.id, document.source_id, document.external_id, document.title,
                    document.url, document.canonical_url, payload["published_at"], payload["fetched_at"],
                    document.summary, document.content, document.content_hash, document.source_tier,
                    document.content_type, document.extraction_quality,
                    json.dumps(document.metadata, ensure_ascii=False),
                ),
            )
        return True

    def update_document_content(self, document: Document) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE documents SET content=?, extraction_quality=?, metadata_json=? WHERE id=?",
                (document.content, document.extraction_quality, json.dumps(document.metadata, ensure_ascii=False), document.id),
            )

    def get_documents(self, ids: list[str]) -> list[Document]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as db:
            rows = db.execute(f"SELECT * FROM documents WHERE id IN ({placeholders})", ids).fetchall()
        by_id = {row["id"]: self._document_from_row(row) for row in rows}
        return [by_id[item] for item in ids if item in by_id]

    def recent_documents(self, since: datetime) -> list[Document]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM documents WHERE published_at>=? ORDER BY published_at DESC",
                (since.isoformat(),),
            ).fetchall()
        return [self._document_from_row(row) for row in rows]

    @staticmethod
    def _document_from_row(row: sqlite3.Row) -> Document:
        return Document(
            id=row["id"], source_id=row["source_id"], source_name=json.loads(row["metadata_json"]).get("source_name", row["source_id"]),
            external_id=row["external_id"], title=row["title"], url=row["url"], canonical_url=row["canonical_url"],
            published_at=row["published_at"], fetched_at=row["fetched_at"], summary=row["summary"], content=row["content"],
            content_hash=row["content_hash"], source_tier=row["source_tier"], content_type=row["content_type"],
            extraction_quality=row["extraction_quality"], metadata=json.loads(row["metadata_json"]),
        )

    def upsert_event(self, event: Event) -> None:
        payload = event.model_dump(mode="json")
        with self._connect() as db:
            db.execute(
                """INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET title=excluded.title, topic_id=excluded.topic_id,
                topic_name=excluded.topic_name, document_ids_json=excluded.document_ids_json,
                first_seen=excluded.first_seen, last_seen=excluded.last_seen,
                source_quality=excluded.source_quality, deterministic_score=excluded.deterministic_score""",
                (
                    event.id, event.title, event.topic_id, event.topic_name,
                    json.dumps(event.document_ids, ensure_ascii=False), payload["first_seen"],
                    payload["last_seen"], event.source_quality, event.deterministic_score,
                ),
            )

    @staticmethod
    def _parse_analysis(row: sqlite3.Row) -> Analysis | None:
        """Rows written by older schema versions may no longer validate; skip them."""
        try:
            return Analysis.model_validate_json(row["analysis_json"])
        except ValidationError:
            return None

    def get_analysis(
        self, event_id: str, cache_scope: str = "default",
    ) -> Analysis | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT analysis_json FROM analysis_variants
                WHERE event_id=? AND cache_scope=?""",
                (event_id, cache_scope),
            ).fetchone()
        return self._parse_analysis(row) if row else None

    def save_analysis(
        self, analysis: Analysis, cache_scope: str = "default",
    ) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO analysis_variants VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(event_id,cache_scope) DO UPDATE SET
                status=excluded.status, model=excluded.model,
                prompt_version=excluded.prompt_version,
                analysis_json=excluded.analysis_json,
                created_at=excluded.created_at""",
                (
                    analysis.event_id, cache_scope, analysis.status.value,
                    analysis.model, analysis.prompt_version,
                    analysis.model_dump_json(), analysis.created_at.isoformat(),
                ),
            )
            db.execute("DELETE FROM evidence WHERE event_id=?", (analysis.event_id,))
            db.executemany(
                "INSERT INTO evidence VALUES(?,?,?,?,?,?)",
                [(analysis.event_id, i, e.document_id, e.url, e.quote, e.locator) for i, e in enumerate(analysis.evidence)],
            )

    def get_latest_analyses(
        self, limit: int, cache_scope: str | None = None,
    ) -> list[Analysis]:
        with self._connect() as db:
            if cache_scope is not None:
                rows = db.execute(
                    """SELECT analysis_json FROM analysis_variants
                    WHERE cache_scope=? ORDER BY created_at DESC LIMIT ?""",
                    (cache_scope, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    """WITH ranked AS (
                        SELECT event_id, analysis_json, created_at,
                               ROW_NUMBER() OVER (
                                   PARTITION BY event_id ORDER BY created_at DESC
                               ) AS position
                        FROM analysis_variants
                    )
                    SELECT analysis_json FROM ranked
                    WHERE position=1
                    ORDER BY created_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
        parsed = [self._parse_analysis(row) for row in rows]
        return [item for item in parsed if item is not None]

    def record_llm_run(self, stage: str, event_id: str | None, model: str, prompt_version: str,
                       input_tokens: int, output_tokens: int, status: str, error: str = "") -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO llm_runs(stage,event_id,model,prompt_version,input_tokens,output_tokens,status,error,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (stage, event_id, model, prompt_version, input_tokens, output_tokens, status, error[:1000], datetime.now().astimezone().isoformat()),
            )

    def start_run(self, metadata: dict[str, Any]) -> int:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO pipeline_runs(started_at,status,metadata_json) VALUES(?,?,?)",
                (datetime.now().astimezone().isoformat(), "running", json.dumps(metadata, ensure_ascii=False)),
            )
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, status: str, metadata: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE pipeline_runs SET finished_at=?, status=?, metadata_json=? WHERE id=?",
                (datetime.now().astimezone().isoformat(), status, json.dumps(metadata, ensure_ascii=False), run_id),
            )

    def get_state(self, key: str) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT value FROM pipeline_state WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def set_state(self, key: str, value: str) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO pipeline_state VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, datetime.now().astimezone().isoformat()),
            )

    def record_publications(self, records: list[PublicationRecord]) -> None:
        if not records:
            return
        with self._connect() as db:
            for record in records:
                payload = record.model_dump(mode="json")
                db.execute(
                    """INSERT INTO publication_ledger(
                        run_id, event_id, canonical_urls_json, experiment_id, is_production,
                        selection_order, publication_tier, research_status, published_at,
                        content_fingerprint, freshness_label
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(run_id, event_id) DO UPDATE SET
                        canonical_urls_json=excluded.canonical_urls_json,
                        experiment_id=excluded.experiment_id,
                        is_production=excluded.is_production,
                        selection_order=excluded.selection_order,
                        publication_tier=excluded.publication_tier,
                        research_status=excluded.research_status,
                        published_at=excluded.published_at,
                        content_fingerprint=excluded.content_fingerprint,
                        freshness_label=excluded.freshness_label
                    """,
                    (
                        record.run_id, record.event_id,
                        json.dumps(record.canonical_urls, ensure_ascii=False),
                        record.experiment_id, 1 if record.is_production else 0,
                        record.selection_order, record.publication_tier,
                        record.research_status, payload["published_at"],
                        record.content_fingerprint, record.freshness_label,
                    ),
                )

    def recent_publications(
        self, since: datetime, until: datetime, *,
        production_only: bool = False, experiment_id: str | None = None,
    ) -> list[PublicationRecord]:
        clauses = ["julianday(published_at)>=julianday(?)", "julianday(published_at)<julianday(?)"]
        values: list[Any] = [since.isoformat(), until.isoformat()]
        if production_only:
            clauses.append("is_production=1")
        elif experiment_id:
            clauses.append("(is_production=1 OR experiment_id=?)")
            values.append(experiment_id)
        sql = (
            "SELECT * FROM publication_ledger WHERE "
            + " AND ".join(clauses)
            + " ORDER BY julianday(published_at) DESC"
        )
        with self._connect() as db:
            rows = db.execute(sql, values).fetchall()
        return [self._publication_from_row(row) for row in rows]

    def first_publication_at(self, event_id: str, *, experiment_id: str, production_only: bool) -> datetime | None:
        clause = "is_production=1" if production_only else "(is_production=1 OR experiment_id=?)"
        args = [event_id] if production_only else [event_id, experiment_id]
        with self._connect() as db:
            rows = db.execute("SELECT published_at FROM publication_ledger WHERE event_id=? AND " + clause, args).fetchall()
        return min((datetime.fromisoformat(row["published_at"]) for row in rows), default=None)

    def save_content_snapshot(self, text: str) -> str:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self._connect() as db:
            db.execute(
                """INSERT INTO content_snapshots(hash, text, created_at)
                VALUES(?,?,?) ON CONFLICT(hash) DO NOTHING""",
                (digest, text, datetime.now().astimezone().isoformat()),
            )
        return digest

    def get_content_snapshot(self, digest: str) -> str | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT text FROM content_snapshots WHERE hash=?", (digest,),
            ).fetchone()
        return None if row is None else str(row["text"])

    def get_successful_stage(self, stage: str, event_id: str, input_hash: str,
                             cache_scope: str, model: str) -> dict | None:
        with self._connect() as db:
            rows = db.execute(
                "SELECT payload_json FROM research_audits WHERE stage=? AND event_id=? "
                "AND snapshot_hash=? AND cache_scope=? AND model=? ORDER BY id DESC",
                (stage + "_attempt", event_id, input_hash, cache_scope, model),
            ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            if payload.get("category") == "success" and payload.get("result") is not None:
                return payload["result"]
        return None

    def save_research_audit(self, payload: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO research_audits(
                    run_id, event_id, stage, model, experiment_id, cache_scope,
                    prompt_version, policy_version, schema_version, snapshot_hash,
                    payload_json, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    int(payload.get("run_id") or 0),
                    str(payload.get("event_id") or ""),
                    str(payload.get("stage") or ""),
                    str(payload.get("model") or ""),
                    str(payload.get("experiment_id") or ""),
                    str(payload.get("cache_scope") or ""),
                    str(payload.get("prompt_version") or ""),
                    str(payload.get("policy_version") or ""),
                    str(payload.get("schema_version") or ""),
                    str(payload.get("snapshot_hash") or ""),
                    json.dumps(payload.get("payload") or {}, ensure_ascii=False),
                    datetime.now().astimezone().isoformat(),
                ),
            )

    def rebuild_publication_ledger(self) -> int:
        with self._connect() as db:
            return self._rebuild_ledger(db)

    def _migrate(self, db: sqlite3.Connection) -> None:
        row = db.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        current = int(row["value"]) if row else 4
        if current < 5:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS publication_ledger (
                    run_id INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    canonical_urls_json TEXT NOT NULL,
                    experiment_id TEXT NOT NULL,
                    is_production INTEGER NOT NULL DEFAULT 0,
                    selection_order INTEGER NOT NULL DEFAULT 0,
                    publication_tier TEXT NOT NULL DEFAULT '',
                    research_status TEXT NOT NULL DEFAULT '',
                    published_at TEXT NOT NULL,
                    content_fingerprint TEXT NOT NULL DEFAULT '',
                    freshness_label TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(run_id, event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_publication_ledger_published
                    ON publication_ledger(published_at);
                CREATE INDEX IF NOT EXISTS idx_publication_ledger_event
                    ON publication_ledger(event_id, published_at);
                CREATE TABLE IF NOT EXISTS content_snapshots (
                    hash TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_audits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL DEFAULT 0,
                    event_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    experiment_id TEXT NOT NULL DEFAULT '',
                    cache_scope TEXT NOT NULL DEFAULT '',
                    prompt_version TEXT NOT NULL DEFAULT '',
                    policy_version TEXT NOT NULL DEFAULT '',
                    schema_version TEXT NOT NULL DEFAULT '',
                    snapshot_hash TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_research_audits_event
                    ON research_audits(event_id, created_at);
                """
            )
            self._rebuild_ledger(db)
        db.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )

    def _rebuild_ledger(self, db: sqlite3.Connection) -> int:
        existing = {
            (int(row["run_id"]), str(row["event_id"]))
            for row in db.execute("SELECT run_id, event_id FROM publication_ledger")
        }
        rows = db.execute(
            "SELECT id, finished_at, metadata_json FROM pipeline_runs WHERE status='success'"
        ).fetchall()
        inserted = 0
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                continue
            experiment_id = str(metadata.get("experiment_id") or "")
            is_production = 1 if experiment_id == "deepseek-v4-flash" else 0
            published_at = str(row["finished_at"] or metadata.get("generated_at") or "")
            intelligence = metadata.get("intelligence") or {}
            for item in intelligence.get("processing_trace") or []:
                if str(item.get("status") or "") != "published":
                    continue
                event_id = str(item.get("event_id") or "")
                key = (int(row["id"]), event_id)
                if not event_id or key in existing:
                    continue
                db.execute(
                    """INSERT INTO publication_ledger(
                        run_id, event_id, canonical_urls_json, experiment_id, is_production,
                        selection_order, publication_tier, research_status, published_at,
                        content_fingerprint, freshness_label
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        int(row["id"]), event_id, "[]", experiment_id, is_production,
                        int(item.get("selection_order") or 0),
                        str(item.get("publication_tier") or ""),
                        "unknown", published_at, "", "unknown",
                    ),
                )
                existing.add(key)
                inserted += 1
        return inserted

    @staticmethod
    def _publication_from_row(row: sqlite3.Row) -> PublicationRecord:
        try:
            urls = json.loads(row["canonical_urls_json"] or "[]")
        except json.JSONDecodeError:
            urls = []
        return PublicationRecord(
            run_id=int(row["run_id"]),
            event_id=str(row["event_id"]),
            canonical_urls=list(urls),
            experiment_id=str(row["experiment_id"]),
            is_production=bool(row["is_production"]),
            selection_order=int(row["selection_order"]),
            publication_tier=str(row["publication_tier"]),
            research_status=str(row["research_status"]),
            published_at=row["published_at"],
            content_fingerprint=str(row["content_fingerprint"] or ""),
            freshness_label=str(row["freshness_label"] or ""),
        )
