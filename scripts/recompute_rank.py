"""用 scout 响应与事件确定性分数复算 rank-fusion 排名。"""
from __future__ import annotations

import json
from pathlib import Path

RUN = Path(r"C:\Users\94202\Desktop\daily\daily-tech-intelligence\output\2026-08-26\runs\110150-568-qwen-code-agent")
request = json.loads((RUN / "harness_io" / "01_scout.request.json").read_text(encoding="utf-8"))
scout = json.loads((RUN / "harness_io" / "01_scout.response.json").read_text(encoding="utf-8"))

payload = json.loads(request["user"])
events = payload["events"]
by_id = {item["event_id"]: item for item in scout["items"]}

ranked = []
for event in events:
    item = by_id.get(event["event_id"])
    if item is None:
        print("MISSING scout item for", event["event_id"])
        continue
    det = event["deterministic_score"]
    if not item["relevant"] and det < 55:
        continue
    if not item["relevant"]:
        score = det
    else:
        model = (item["relevance"] * .30 + item["novelty"] * .25
                 + item["technical_depth"] * .25 + item["industry_impact"] * .20)
        score = det * .65 + model * .35
    ranked.append((score, event["topic_hint"], event["event_id"], event["title"][:40], det, item["relevant"]))

ranked.sort(key=lambda x: x[0], reverse=True)
print("== ranked top 12 ==")
for r in ranked[:12]:
    print(f"  {r[0]:7.3f}  det={r[4]:6.2f} rel={r[5]}  [{r[1]}] {r[2]} {r[3]}")

# balance
first, rest, seen = [], [], set()
for r in ranked:
    if r[1] not in seen:
        seen.add(r[1])
        first.append(r)
    else:
        rest.append(r)
balanced = first + rest
print("\n== balanced top 8 ==")
for r in balanced[:8]:
    print(f"  {r[0]:7.3f}  [{r[1]}] {r[2]} {r[3]}")
