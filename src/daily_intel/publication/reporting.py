from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, PackageLoader, select_autoescape

from daily_intel.core.models import Analysis
from daily_intel.core.runs import sanitize_run_identifier
from daily_intel.market.normalize import clean_text
from daily_intel.publication.plain_digest import (
    build_plain_digest,
    group_analyses_by_topic,
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _shanghai(value: datetime | None) -> str:
    if value is None:
        return "日期未知"
    current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return current.astimezone(_SHANGHAI).strftime("%Y-%m-%d %H:%M")


QUALITY_ISSUE_LABELS = {
    "verifier_not_pass": "校验器未通过",
    "unsupported_claims": "存在未支持结论",
    "conflicting_claims": "存在与来源冲突的结论",
    "unverifiable_claims": "存在无法核验的结论",
    "claim_protocol_error": "审核返回的断言编号重复或超出清单",
    "unaudited_claims": "关键结论未经逐项审核",
    "rewritten_after_audit": "审核后发生实质性改写",
    "insufficient_evidence": "可定位证据不足",
    "missing_primary_source": "缺少一手来源",
    "insufficient_key_facts": "有效事实不足",
    "missing_plain_takeaway": "缺少大白话要点",
    "missing_required_sections": "必填分析段缺失",
    "insufficient_risks": "风险项不足",
    "insufficient_counterpoints": "反面观点不足",
    "ai_not_enabled": "AI未启用",
    "research_failed": "已列为研究目标，深研失败",
    "broad_reading_only": "仅完成泛读，未进入前十研究",
}


def quality_issue_label(value: str) -> str:
    return QUALITY_ISSUE_LABELS.get(value, value)


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
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters.update(text=clean_text)
    env.filters["quality_issue"] = quality_issue_label
    env.filters["shanghai"] = _shanghai
    html_path = run_dir / "daily_digest.html"
    if html_path.exists():
        raise FileExistsError(f"运行目录已包含日报文件，拒绝覆盖: {run_dir}")

    intensive_analyses = group_analyses_by_topic(list(
        context["intensive_analyses"] if "intensive_analyses" in context else analyses
    ))
    extensive_analyses = list(context.get("extensive_analyses") or [])
    draft = build_plain_digest(intensive_analyses, context)
    extensive_digest = build_plain_digest(extensive_analyses, context)
    extensive_items = extensive_digest["tech_items"]
    for index, item in enumerate(extensive_items, start=1):
        item["detail_id"] = f"extensive-item-{index}"
    digest = {
        **draft,
        "has_content": bool(draft["tech_items"] or extensive_digest["tech_items"]),
    }
    render_context = {
        "model_runtime": {},
        "quality_summary": {},
        "github_projects": [],
        **context,
        "intensive_analyses": intensive_analyses,
        "extensive_items": extensive_items,
        "extensive_groups": group_scan_items(extensive_items),
        "plain_digest": digest,
    }
    html_path.write_text(
        env.get_template("report.html.j2").render(**render_context), encoding="utf-8"
    )
    return {"html": html_path}
