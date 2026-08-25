"""检查情报数据库当前状态。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "intelligence.db"

con = sqlite3.connect(DB)
cur = con.cursor()

print("== analyses ==")
for row in cur.execute("select event_id, status, model, prompt_version, created_at from analyses"):
    print(" ", row)

print("\n== pipeline_state ==")
for key, value in cur.execute("select key, value from pipeline_state"):
    if key.startswith("scout_selection"):
        try:
            payload = json.loads(value)
            print(f"  {key} = event_ids: {payload.get('event_ids')}")
        except Exception:
            print(f"  {key} = {value[:200]}")
    else:
        print(f"  {key} = {value[:120]}")

print("\n== llm_runs ==")
for row in cur.execute("select stage, event_id, model, status, substr(coalesce(error,''),1,150) from llm_runs order by rowid"):
    print(" ", row)

print("\n== events (top by deterministic_score) ==")
for row in cur.execute("select id, substr(title,1,60), topic_id, source_quality, deterministic_score from events order by deterministic_score desc limit 12"):
    print(" ", row)
