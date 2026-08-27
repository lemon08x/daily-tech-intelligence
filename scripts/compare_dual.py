"""对比双模型日报结果(DeepSeek vs Qwen) —— 按 event_id 对齐事件。

用法:
  python scripts/compare_dual.py
  python scripts/compare_dual.py --deepseek output/2026-08-27_deepseek --qwen output/2026-08-27_qwen
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT = PROJECT_ROOT / "output"


def load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def latest_intelligence(base: Path) -> Path:
    candidates = list(base.glob("*/runs/*/intelligence.json")) + list(base.glob("runs/*/intelligence.json"))
    if not candidates:
        raise FileNotFoundError(f"未找到 intelligence.json: {base}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?%?|\d+\.\d+", text or "")


def norm_headline(h: str) -> str:
    """去掉措辞差异,保留核心标识词。"""
    for token in ("SRPO", "ConvergeFlow", "EvoMax", "Fanzor", "FanzMAX", "PCSK9",
                  "NVIDIA", "Groq", "PHASE", "GeoWAM", "BPCO", "广汽", "华为"):
        if token in h:
            return token
    return h[:20]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deepseek", type=Path, default=OUT / f"{date.today().isoformat()}_deepseek")
    parser.add_argument("--qwen", type=Path, default=OUT / f"{date.today().isoformat()}_qwen")
    parser.add_argument("--out", type=Path, default=OUT / f"{date.today().isoformat()}_compare")
    args = parser.parse_args()
    ds_path = latest_intelligence(args.deepseek)
    qw_path = latest_intelligence(args.qwen)
    deepseek = load(ds_path)
    qwen = load(qw_path)

    ds_by_id = {a["event_id"]: a for a in deepseek["analyses"]}
    qw_by_id = {a["event_id"]: a for a in qwen["analyses"]}

    ds_ids = set(ds_by_id)
    qw_ids = set(qw_by_id)
    overlap = ds_ids & qw_ids

    lines: list[str] = []
    lines.append("# 双模型结果对比: DeepSeek V4 Flash vs Qwen3.8 27B")
    lines.append("")
    lines.append(f"- DeepSeek 产物: `{ds_path.relative_to(PROJECT_ROOT).as_posix()}`")
    lines.append(f"- Qwen 产物: `{qw_path.relative_to(PROJECT_ROOT).as_posix()}`")
    lines.append(f"- DeepSeek 分析数: {len(ds_by_id)} (深度 {sum(1 for a in ds_by_id.values() if a['status']=='deep')})")
    lines.append(f"- Qwen 分析数: {len(qw_by_id)} (深度 {sum(1 for a in qw_by_id.values() if a['status']=='deep')})")
    lines.append(f"- 按 event_id 对齐: 重叠 {len(overlap)} 条, DeepSeek 独有 {len(ds_ids-qw_ids)} 条, Qwen 独有 {len(qw_ids-ds_ids)} 条")
    lines.append("")

    lines.append("## 一、事件选择(选题偏好)")
    lines.append("")
    lines.append("### 重叠事件(双方都选)")
    for eid in sorted(overlap):
        lines.append(f"- **{norm_headline(ds_by_id[eid]['headline'])}**")
        lines.append(f"  - DeepSeek: {ds_by_id[eid]['headline']}")
        lines.append(f"  - Qwen:     {qw_by_id[eid]['headline']}")
    lines.append("")
    lines.append("### DeepSeek 独有")
    for eid in sorted(ds_ids - qw_ids):
        a = ds_by_id[eid]
        lines.append(f"- [{a['status']}] {a['headline']}")
    lines.append("")
    lines.append("### Qwen 独有")
    for eid in sorted(qw_ids - ds_ids):
        a = qw_by_id[eid]
        lines.append(f"- [{a['status']}] {a['headline']}")
    lines.append("")

    lines.append("## 二、重叠事件的深研质量对比")
    lines.append("")
    for eid in sorted(overlap):
        ds, qw = ds_by_id[eid], qw_by_id[eid]
        lines.append(f"### {norm_headline(ds['headline'])}")
        lines.append("")
        lines.append(f"| 维度 | DeepSeek | Qwen |")
        lines.append(f"|---|---|---|")
        lines.append(f"| 状态 | {ds['status']} | {qw['status']} |")
        lines.append(f"| 置信度 | {ds['confidence']:.0%} | {qw['confidence']:.0%} |")
        lines.append(f"| 大白话要点 | {(ds.get('plain_takeaway') or '—')[:80]} | {(qw.get('plain_takeaway') or '—')[:80]} |")
        lines.append(f"| 关键事实条数 | {len(ds['key_facts'])} | {len(qw['key_facts'])} |")
        lines.append(f"| 证据条数 | {len(ds['evidence'])} | {len(qw['evidence'])} |")
        lines.append(f"| 风险+反面 | {len(ds.get('risks',[]))+len(ds.get('counterpoints',[]))} | {len(qw.get('risks',[]))+len(qw.get('counterpoints',[]))} |")
        lines.append(f"| A股关联 | {len(ds.get('company_mappings',[]))} | {len(qw.get('company_mappings',[]))} |")
        lines.append("")
        ds_nums = set(numbers(" ".join(ds["key_facts"])))
        qw_nums = set(numbers(" ".join(qw["key_facts"])))
        lines.append(f"- 双方共同关键数字: {sorted(ds_nums & qw_nums)}")
        lines.append(f"- DeepSeek 独有数字: {sorted(ds_nums - qw_nums)}")
        lines.append(f"- Qwen 独有数字: {sorted(qw_nums - ds_nums)}")
        lines.append("")
        lines.append("DeepSeek 关键事实:")
        for f in ds["key_facts"]:
            lines.append(f"  - {f[:150]}")
        lines.append("Qwen 关键事实:")
        for f in qw["key_facts"]:
            lines.append(f"  - {f[:150]}")
        lines.append("")

    lines.append("## 三、总体质量维度")
    lines.append("")
    for label, data in (("DeepSeek", deepseek), ("Qwen", qwen)):
        analyses = data["analyses"]
        total_evid = sum(len(a["evidence"]) for a in analyses)
        total_risk = sum(len(a.get("risks", [])) + len(a.get("counterpoints", [])) for a in analyses)
        total_facts = sum(len(a.get("key_facts", [])) for a in analyses)
        avg_conf = sum(a["confidence"] for a in analyses) / len(analyses) if analyses else 0
        lines.append(f"### {label}")
        lines.append(f"- 平均置信度: {avg_conf:.0%}")
        lines.append(f"- 证据总数: {total_evid} (平均 {total_evid/max(len(analyses),1):.1f}/条)")
        lines.append(f"- 风险/反面总数: {total_risk} (平均 {total_risk/max(len(analyses),1):.1f}/条)")
        lines.append(f"- 关键事实总数: {total_facts}")
        lines.append("")

    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "comparison.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"对比报告已写入: {out_dir / 'comparison.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())