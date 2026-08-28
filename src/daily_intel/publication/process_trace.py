from __future__ import annotations

from typing import Any

from daily_intel.core.models import Analysis
from daily_intel.market.normalize import clean_text


SELECTION_LABELS = {
    "scout_rejected": "Scout 剔除",
    "scout_missing": "Scout 未覆盖，沿用确定性分",
    "scout_cached": "复用 Scout 排序",
    "kept": "进入排序",
    "deterministic": "无模型排序",
}
RESEARCH_LABELS = {
    "lane_cap": "栏位已满，未深研",
    "cache": "复用已有分析",
    "analyzed": "新深研",
    "lead": "无 AI，仅线索",
    "failed": "深研失败，已跳过",
}


def _label(mapping: dict[str, str], value: str) -> str:
    return mapping.get(value, value or "—")


def _slim_news(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    slim: list[dict[str, Any]] = []
    for item in rows:
        slim.append({
            "title": item.get("title") or "",
            "url": item.get("url") or "",
            "summary": clean_text(str(item.get("summary") or ""), 180),
            "impact": clean_text(str(item.get("impact") or ""), 220),
            "consequences": clean_text(str(item.get("consequences") or ""), 220),
            "reasoning": clean_text(str(item.get("reasoning") or ""), 280),
            "quotes": list(item.get("quotes") or []),
        })
    return slim


def assemble_process(
    context: dict[str, Any],
    analyses: list[Analysis],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    provided = dict(context.get("process") or {})
    intelligence = dict(provided.get("intelligence") or {})
    market = dict(provided.get("market") or metadata.get("process") or {})
    github = dict(provided.get("github") or {})
    briefing = dict(provided.get("briefing") or {})
    if not github:
        github = {
            "sources": list(context.get("github_source_status") or []),
            "projects": [
                {
                    "full_name": item.get("full_name"),
                    "origin": item.get("origin_label") or item.get("origin") or "GitHub",
                    "reason": item.get("reason"),
                    "url": item.get("url"),
                    "plain": item.get("plain"),
                }
                for item in list(context.get("github_projects") or [])
            ],
            "errors": [],
        }
    if not briefing.get("scan_paragraph"):
        briefing["scan_paragraph"] = context.get("scan_paragraph") or (
            (context.get("plain_digest") or {}).get("scan_paragraph") or ""
        )
    if not market.get("news_selected"):
        market["news_selected"] = _slim_news(list(context.get("news_records") or []))

    selection = []
    for item in intelligence.get("selection") or []:
        row = dict(item)
        row["action_label"] = _label(SELECTION_LABELS, str(row.get("action") or ""))
        selection.append(row)
    research = []
    for item in intelligence.get("research") or []:
        row = dict(item)
        row["decision_label"] = _label(RESEARCH_LABELS, str(row.get("decision") or ""))
        research.append(row)
    if not research:
        for analysis in analyses:
            research.append({
                "event_id": analysis.event_id,
                "title": analysis.headline,
                "lane": analysis.lane,
                "decision": "published",
                "decision_label": "已发布",
                "status": analysis.status.value,
                "quality_score": analysis.quality.score,
                "quality_passed": analysis.quality.passed,
                "issues": list(analysis.quality.issues),
                "plain_takeaway": analysis.plain_takeaway,
            })

    quality = context.get("quality_summary") or metadata.get("intelligence", {}).get("quality") or {}
    return {
        "title": f"处理过程 · {context.get('title') or '科技产业情报日报'}",
        "report_date": context.get("report_date") or "",
        "generated_at": context.get("generated_at") or metadata.get("generated_at") or "",
        "run_name": context.get("run_name") or metadata.get("run_name") or "",
        "experiment_id": context.get("experiment_id") or metadata.get("experiment_id") or "",
        "ai_status_label": context.get("ai_status_label") or context.get("ai_status") or "",
        "prompt_version": context.get("prompt_version") or "",
        "model_runtime": context.get("model_runtime") or metadata.get("ai") or {},
        "usage": context.get("usage") or {},
        "quality": quality,
        "errors": list(context.get("pipeline_errors") or []),
        "market_sources": list(context.get("market_source_status") or []),
        "intelligence_sources": list(context.get("intelligence_source_status") or []),
        "github_sources": list(github.get("sources") or []),
        "market": {
            **market,
            "news_selected": _slim_news(list(market.get("news_selected") or [])),
            "industry_bars": list((context.get("plain_digest") or {}).get("industry_bars") or []),
            "board": list((context.get("plain_digest") or {}).get("board") or []),
        },
        "documents": list(intelligence.get("documents") or []),
        "events": list(intelligence.get("events") or []),
        "selection": selection,
        "research": research,
        "limits": intelligence.get("limits") or {},
        "cache_scope": intelligence.get("cache_scope") or (
            (metadata.get("intelligence") or {}).get("cache") or {}
        ).get("scope") or "",
        "cache": (metadata.get("intelligence") or {}).get("cache") or {},
        "github_projects": list(github.get("projects") or []),
        "briefing": briefing,
        "published": [
            {
                "headline": item.headline,
                "lane": item.lane,
                "status": item.status.value,
                "quality_score": item.quality.score,
                "issues": list(item.quality.issues),
                "url": item.evidence[0].url if item.evidence else "",
            }
            for item in analyses
        ],
    }
