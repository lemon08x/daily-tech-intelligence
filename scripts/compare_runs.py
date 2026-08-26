"""对比新旧两次运行的分析质量与证据校验结果。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "output" / "2026-08-25"
NEW = ROOT / "runs" / "180201-202-qwen-code-agent" / "intelligence.json"
OLD = ROOT / "runs" / "legacy-qwen-code-agent" / "intelligence.json"


def show(path: Path, label: str) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    print(f"== {label} ==")
    for a in data["analyses"]:
        q = a.get("quality", {})
        ev = a.get("evidence", [])
        urls = {e["url"] for e in ev}
        print(f"  {a['event_id']} | {a['status']} | score={q.get('score')} | issues={q.get('issues')}")
        print(f"    supported={q.get('supported_evidence')} primary={q.get('primary_sources')} diversity={q.get('source_diversity')} evidence={len(ev)}")
        print(f"    confidence={a.get('confidence')} headline={a.get('headline','')[:44]}")
    print()


show(OLD, "旧运行 (legacy, 上午)")
show(NEW, "新运行 (174956, 下午)")
