from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

from jinja2 import Environment, PackageLoader

from daily_intel.core.models import Analysis
from daily_intel.core.runs import sanitize_run_identifier
from daily_intel.market.normalize import clean_text
from daily_intel.publication.verification import verification_status
from daily_intel.publication.plain_digest import (
    build_plain_digest,
    group_analyses_by_topic,
    display_headline, audit_notes,
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _shanghai(value: datetime | None) -> str:
    if value is None:
        return "日期未知"
    current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return current.astimezone(_SHANGHAI).strftime("%Y-%m-%d %H:%M")


QUALITY_ISSUE_LABELS = {
    "verifier_not_pass": "部分内容未通过证据核验",
    "unsupported_claims": "部分推断缺少原文支持",
    "conflicting_claims": "部分表述与原文不一致",
    "unverifiable_claims": "部分内容暂时无法核实",
    "claim_protocol_error": "核验结果不完整",
    "unaudited_claims": "部分关键内容尚未核实",
    "rewritten_after_audit": "修改后的内容需要重新核实",
    "insufficient_evidence": "可供核对的原文不足",
    "missing_primary_source": "缺少一手来源",
    "insufficient_key_facts": "已核实的事实不足",
    "missing_plain_takeaway": "尚未形成有依据的摘要",
    "missing_required_sections": "现有材料不足以支持完整分析",
    "insufficient_risks": "风险信息不足",
    "insufficient_counterpoints": "缺少反面信息",
    "ai_not_enabled": "AI未启用",
    "research_failed": "本条未完成核验",
    "broad_reading_only": "本条为初步整理，尚未逐项核验",
}


def quality_issue_label(value: str) -> str:
    return QUALITY_ISSUE_LABELS.get(value, "部分内容仍需核实")


def reader_notice(value: str) -> str:
    """Translate legacy cached workflow notices without altering source quotes."""
    if "材料不足" in value and ("深研" in value or "泛读" in value):
        return "原文材料不足，仅供线索参考"
    if "质量门" in value or "Analyst" in value or "Verifier" in value:
        return "本条尚未完成充分核验，仅供线索参考"
    if "未进入前" in value:
        return "初步整理，尚未逐项核验"
    return value


def group_scan_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group scan lines by topic while preserving Scout order within each group."""
    groups: dict[str, dict[str, Any]] = {}
    for item in items:
        label = str(item.get("kicker") or "其他科技").strip() or "其他科技"
        group = groups.setdefault(label, {"label": label, "items": []})
        group["items"].append(item)
    return list(groups.values())


def publish(
    context: dict[str, Any], analyses: list[Analysis], metadata: dict[str, Any],
    output_dir: Path, now: datetime,
) -> dict[str, Path]:
    day_dir = output_dir / now.strftime("%Y-%m-%d")
    run_name = sanitize_run_identifier(
        str(metadata.get("run_name") or f"{now:%H%M%S}-default")
    )
    run_dir = day_dir / "runs" / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    env = Environment(
        loader=PackageLoader("daily_intel", "publication/templates"),
        autoescape=True,
    )
    env.filters.update(text=clean_text)
    env.filters["quality_issue"] = quality_issue_label
    env.filters["reader_notice"] = reader_notice
    env.filters["shanghai"] = _shanghai
    env.filters["display_headline"] = display_headline
    env.filters["audit_notes"] = audit_notes
    env.filters["verification_status"] = verification_status
    html_path = run_dir / "daily_digest.html"
    if html_path.exists():
        raise FileExistsError(f"运行目录已包含日报文件，拒绝覆盖: {run_dir}")

    intensive_analyses = group_analyses_by_topic(list(
        context["intensive_analyses"] if "intensive_analyses" in context else analyses
    ))
    extensive_analyses = list(context.get("extensive_analyses") or [])
    draft = build_plain_digest(intensive_analyses or extensive_analyses[:7], context)
    extensive_digest = build_plain_digest(extensive_analyses, context)
    extensive_items = extensive_digest["tech_items"]
    for index, item in enumerate(extensive_items, start=1):
        item["detail_id"] = f"extensive-item-{index}"
    digest = {
        **draft,
        "has_content": bool(draft["tech_items"] or extensive_digest["tech_items"]),
    }
    source_events: dict[str, set[str]] = {}
    source_urls: dict[str, str] = {}
    for analysis in analyses:
        for evidence in analysis.evidence:
            try:
                parsed = urlsplit(evidence.url)
            except ValueError:
                continue
            host = (parsed.hostname or "").removeprefix("www.")
            if not host or parsed.scheme not in {"https", "http"}:
                continue
            source_events.setdefault(host, set()).add(analysis.event_id)
            source_urls.setdefault(host, evidence.url)
    cited_sources = [
        {"name": host, "url": source_urls[host], "count": len(events)}
        for host, events in sorted(source_events.items(), key=lambda pair: (-len(pair[1]), pair[0]))
    ]
    source_gaps = list(dict.fromkeys(
        metadata.get("freshness", {}).get("failed_or_cached_sources", [])
        + [item["name"] for key in ("intelligence_source_status", "market_source_status", "github_source_status")
           for item in context.get(key, []) if item.get("stale") and item.get("name")]
    ))
    arxiv_count = len(source_events.get("arxiv.org", set()))
    coverage_note = (
        f"本报 {len(analyses)} 条科技信息中，{arxiv_count} 条引用 arXiv，内容偏重研究论文，不能代表完整的产业新闻覆盖。"
        if analyses and arxiv_count > len(analyses) / 2 else ""
    )
    render_context = {
        "model_runtime": {},
        "quality_summary": {},
        "github_projects": [],
        **context,
        "verified_count": sum(a.status.value == "deep" and a.quality.passed for a in intensive_analyses),
        "intensive_analyses": intensive_analyses,
        "extensive_items": extensive_items,
        "extensive_groups": group_scan_items(extensive_items),
        "plain_digest": digest,
        "cited_sources": cited_sources,
        "source_gaps": source_gaps,
        "source_coverage_note": coverage_note,
    }
    html_path.write_text(
        env.get_template("report.html.j2").render(**render_context), encoding="utf-8"
    )
    return {"html": html_path}
