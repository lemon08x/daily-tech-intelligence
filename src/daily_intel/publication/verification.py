"""Reader-facing audit states, separate from the deep-publication gate."""
from daily_intel.core.models import Analysis


def verification_status(analysis: Analysis) -> dict:
    quality = analysis.quality
    issues = set(quality.issues)
    claims = quality.claim_audits
    supported = sum(c.status.value == "supported" for c in claims)
    source = "含一手材料" if quality.primary_sources else "缺少一手材料"
    if analysis.publication.research_status == "research_failed" or "research_failed" in issues:
        state, label = "incomplete", "核验未完成"
    elif "broad_reading_only" in issues or "ai_not_enabled" in issues:
        state, label = "unreviewed", "未做逐项核验"
    elif quality.final_content_passed:
        state, label = ("verified", "刊登内容已核验") if quality.primary_sources else ("source_limited", "已核对报道")
    elif analysis.status.value == "deep" and quality.passed:
        state, label = "verified", "已对照原文核验"
    elif claims and supported == len(claims) and issues == {"missing_primary_source"}:
        state, label = "source_limited", "已核对报道"
    elif supported:
        state, label = "partial", "部分核验通过"
    elif claims:
        state, label = "unsupported", "尚无通过核验的断言"
    else:
        state, label = "unreviewed", "未做逐项核验"
    detail = f"草稿 {len(claims)} 项断言中 {supported} 项获原文支持" if claims else label
    if quality.final_content_passed:
        detail = f"刊登内容 {quality.final_claim_count} 项断言已对照原文；草稿移除 {quality.removed_claim_count} 项未通过断言"
        if quality.completeness_issues:
            detail += "；部分分析栏目证据不足，已省略"
    if state == "partial":
        detail += "；查看核验限制"
    elif state == "source_limited":
        detail += "；已对照报道核对，尚缺一手材料交叉确认"
    if analysis.publication.material_gate_reason or analysis.publication.research_status == "material_gate_extensive":
        detail += "；原文材料不足，仅供线索参考"
    return {"state": state, "label": label, "source": source, "detail": detail,
            "supported": supported, "total": len(claims)}
