"""把 llm_io 请求文件中的 user payload 格式化打印，便于代理审阅。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

IO_DIR = Path(__file__).resolve().parent.parent / "llm_io"


def main() -> None:
    request_path = Path(sys.argv[1]) if len(sys.argv) > 1 else sorted(IO_DIR.glob("*.request.json"))[-1]
    data = json.loads(request_path.read_text(encoding="utf-8"))
    print(f"== {request_path.name} | stage={data['stage']} | schema={data['schema']}")
    user = data["user"]
    try:
        payload = json.loads(user)
    except json.JSONDecodeError:
        print(user[:4000])
        return
    if "events" in payload:  # scout
        for i, e in enumerate(payload["events"], 1):
            srcs = e.get("sources", [])
            names = " ; ".join(s["name"] for s in srcs)
            print(f"\n{i}. [{e['event_id']}] score={e['deterministic_score']} topic={e['topic_hint']}")
            print(f"   title: {e['title']}")
            print(f"   sources({len(srcs)}): {names}")
            for s in srcs[:3]:
                print(f"   - [{s['name']}|tier{s['tier']}] {s['title']}")
                print(f"     {s['summary'][:600]}")
    elif "documents" in payload:  # analyst / verifier
        ev = payload.get("event", {})
        print(f"\nevent: [{ev.get('id')}] {ev.get('title')} (topic={ev.get('topic_id')})")
        for doc in payload.get("documents", []):
            content = doc.get("content", "")
            print(f"\ndoc: [{doc.get('document_id')}] {doc.get('source', '')} tier={doc.get('source_tier')}")
            print(f"   url: {doc.get('url')}")
            print(f"   title: {doc.get('title')}")
            print(f"   content_len: {len(content)}")
            print(f"   content_head: {content[:800]}")
        if "draft" in payload:
            print("\n--- draft ---")
            print(json.dumps(payload["draft"], ensure_ascii=False, indent=1)[:6000])
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=1)[:8000])


if __name__ == "__main__":
    main()
