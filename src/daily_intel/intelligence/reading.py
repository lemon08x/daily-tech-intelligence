from __future__ import annotations

import re

from daily_intel.core.models import Analysis


MIN_INTENSIVE_EVIDENCE_ITEMS = 2
MIN_INTENSIVE_EVIDENCE_CHARS = 160


def evidence_material_chars(analysis: Analysis) -> int:
    """Count distinct, located evidence without double-counting contained quotes."""
    quotes = {
        re.sub(r"\s+", " ", item.quote).strip()
        for item in analysis.evidence
        if item.quote.strip()
    }
    distinct: list[str] = []
    for quote in sorted(quotes, key=len, reverse=True):
        if any(quote in longer for longer in distinct):
            continue
        distinct.append(quote)
    return sum(len(item) for item in distinct)


def intensive_material_issue(analysis: Analysis) -> str:
    """Return why an AI result is too thin for a full intensive-reading card."""
    if "broad_reading_only" in analysis.quality.issues:
        return "仅完成批量泛读，未执行 Analyst 与 Verifier"
    # The explicit no-AI fallback is already labelled as an unresearched lead;
    # preserve its deterministic ranking instead of pretending it passed AI QA.
    if analysis.model == "none":
        return ""
    supported = min(analysis.quality.supported_evidence, len(analysis.evidence))
    if supported < MIN_INTENSIVE_EVIDENCE_ITEMS:
        return f"有效证据少于 {MIN_INTENSIVE_EVIDENCE_ITEMS} 条"
    material_chars = evidence_material_chars(analysis)
    if material_chars < MIN_INTENSIVE_EVIDENCE_CHARS:
        return (
            f"去重后的可定位证据仅 {material_chars} 字，"
            f"低于 {MIN_INTENSIVE_EVIDENCE_CHARS} 字"
        )
    return ""


def partition_reading_analyses(
    analyses: list[Analysis], intensive_limit: int,
) -> tuple[list[Analysis], list[Analysis]]:
    """Fill intensive reading in Scout order and send thin results to scanning."""
    limit = max(0, intensive_limit)
    intensive: list[Analysis] = []
    extensive: list[Analysis] = []
    for analysis in analyses:
        if len(intensive) < limit and not intensive_material_issue(analysis):
            intensive.append(analysis)
        else:
            extensive.append(analysis)
    return intensive, extensive
