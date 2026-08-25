"""检查指定事件的文档状态。"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "data" / "intelligence.db"
EVENT_IDS = sys.argv[1:]

if not EVENT_IDS:
    raise SystemExit("用法: python scripts/diagnostics/inspect_events.py <event-id> [...]")

con = sqlite3.connect(DB)
cur = con.cursor()
for event_id in EVENT_IDS:
    row = cur.execute("select id, title, topic_id, document_ids_json from events where id=?", (event_id,)).fetchone()
    if not row:
        print(f"{event_id}: NOT IN EVENTS TABLE")
        continue
    print(f"event {row[0]}: {row[1][:60]} | topic={row[2]}")
    doc_ids = json.loads(row[3])
    print(f"  document_ids: {doc_ids}")
    for doc_id in doc_ids:
        drow = cur.execute(
            "select id, source_id, substr(title,1,40), length(content), extraction_quality, substr(coalesce(metadata_json,'{}'),1,120) from documents where id=?",
            (doc_id,),
        ).fetchone()
        print(f"  doc: {drow}")
    print()
