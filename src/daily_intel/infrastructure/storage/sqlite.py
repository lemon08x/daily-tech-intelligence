from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from daily_intel.core.models import Analysis, Document, Event


SCHEMA_VERSION = 1


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

                CREATE TABLE IF NOT EXISTS analyses (
                    event_id TEXT PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    analysis_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence (
                    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    document_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    quote TEXT NOT NULL,
                    locator TEXT NOT NULL,
                    PRIMARY KEY(event_id, position)
                );
                CREATE TABLE IF NOT EXISTS industry_mappings (
                    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(event_id, position)
                );
                CREATE TABLE IF NOT EXISTS company_mappings (
                    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
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
            db.execute(
                "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    def upsert_document(self, document: Document) -> bool:
        payload = document.model_dump(mode="json")
        with self._connect() as db:
            existing = db.execute(
                "SELECT id FROM documents WHERE content_hash=? OR (source_id=? AND external_id=?)",
                (document.content_hash, document.source_id, document.external_id),
            ).fetchone()
            if existing:
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

    def get_analysis(self, event_id: str) -> Analysis | None:
        with self._connect() as db:
            row = db.execute("SELECT analysis_json FROM analyses WHERE event_id=?", (event_id,)).fetchone()
        return Analysis.model_validate_json(row["analysis_json"]) if row else None

    def save_analysis(self, analysis: Analysis) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO analyses VALUES(?,?,?,?,?,?)
                ON CONFLICT(event_id) DO UPDATE SET status=excluded.status, model=excluded.model,
                prompt_version=excluded.prompt_version, analysis_json=excluded.analysis_json,
                created_at=excluded.created_at""",
                (analysis.event_id, analysis.status.value, analysis.model, analysis.prompt_version,
                 analysis.model_dump_json(), analysis.created_at.isoformat()),
            )
            for table in ("evidence", "industry_mappings", "company_mappings"):
                db.execute(f"DELETE FROM {table} WHERE event_id=?", (analysis.event_id,))
            db.executemany(
                "INSERT INTO evidence VALUES(?,?,?,?,?,?)",
                [(analysis.event_id, i, e.document_id, e.url, e.quote, e.locator) for i, e in enumerate(analysis.evidence)],
            )
            db.executemany(
                "INSERT INTO industry_mappings VALUES(?,?,?)",
                [(analysis.event_id, i, item.model_dump_json()) for i, item in enumerate(analysis.industry_impacts)],
            )
            db.executemany(
                "INSERT INTO company_mappings VALUES(?,?,?)",
                [(analysis.event_id, i, item.model_dump_json()) for i, item in enumerate(analysis.company_mappings)],
            )

    def get_latest_analyses(self, limit: int) -> list[Analysis]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT analysis_json FROM analyses ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Analysis.model_validate_json(row["analysis_json"]) for row in rows]

    def record_llm_run(self, stage: str, event_id: str | None, model: str, prompt_version: str,
                       input_tokens: int, output_tokens: int, status: str, error: str = "") -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO llm_runs(stage,event_id,model,prompt_version,input_tokens,output_tokens,status,error,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (stage, event_id, model, prompt_version, input_tokens, output_tokens, status, error[:1000], datetime.now().astimezone().isoformat()),
            )

    def llm_usage_since(self, since: datetime) -> dict[str, int]:
        with self._connect() as db:
            row = db.execute(
                "SELECT COALESCE(SUM(input_tokens),0) AS i, COALESCE(SUM(output_tokens),0) AS o, COUNT(*) AS c FROM llm_runs WHERE created_at>=? AND status='success'",
                (since.isoformat(),),
            ).fetchone()
        return {"input_tokens": int(row["i"]), "output_tokens": int(row["o"]), "calls": int(row["c"])}

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
