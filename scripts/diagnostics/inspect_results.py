"""最终结果检查。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "data" / "intelligence.db"
con = sqlite3.connect(DB)
cur = con.cursor()

print("== analyses ==")
for event_id, status, analysis_json in cur.execute("select event_id, status, analysis_json from analyses"):
    data = json.loads(analysis_json)
    print(f"  {event_id} | {status} | {data['headline'][:60]}")
    print(f"    confidence={data['confidence']} evidence={len(data['evidence'])}")
