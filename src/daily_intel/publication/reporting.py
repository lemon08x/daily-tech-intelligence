from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

from daily_intel.core.models import Analysis
from daily_intel.core.runs import sanitize_run_identifier
from daily_intel.market.normalize import clean_text
from daily_intel.publication.plain_digest import (
    build_plain_digest,
    group_analyses_by_topic,
)


QUALITY_ISSUE_LABELS = {
    "verifier_not_pass": "校验器未通过",
    "unsupported_claims": "存在未支持结论",
    "insufficient_evidence": "可定位证据不足",
    "missing_primary_source": "缺少一手来源",
    "insufficient_key_facts": "有效事实不足",
    "missing_plain_takeaway": "缺少大白话要点",
    "missing_required_sections": "必填分析段缺失",
    "insufficient_risks": "风险项不足",
    "insufficient_counterpoints": "反面观点不足",
    "ai_not_enabled": "AI未启用",
    "broad_reading_only": "仅完成泛读，未进入前十精读",
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
