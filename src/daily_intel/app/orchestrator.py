from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from daily_intel.core.ports import (
    DigestPublisher,
    IntelligenceRepository,
    IntelligenceWorkflow,
    LLMClient,
    MarketWorkflow,
)
from daily_intel.core.progress import progress
from daily_intel.core.settings import resolve_path
from daily_intel.core.runs import sanitize_run_identifier
from daily_intel.github.pipeline import GitHubTrendingPipeline, apply_git_brief
from daily_intel.infrastructure.http import install_proxy_fallback
from daily_intel.infrastructure.llm.openai_compatible import OpenAICompatibleLLM
from daily_intel.infrastructure.storage.sqlite import SQLiteIntelligenceRepository
from daily_intel.intelligence.pipeline import IntelligencePipeline
from daily_intel.intelligence.reading import (
    intensive_material_issue,
    partition_reading_analyses,
)
from daily_intel.market.pipeline import MarketPipeline
from daily_intel.publication.publisher import FileDigestPublisher


AI_STATUS_LABELS = {
    "enabled": "AI深研已启用",
    "disabled": "AI未启用，展示权威来源线索",
    "cached": "离线复用已存分析",
    "unavailable": "离线且没有已存分析",
}


def run_application(
    settings: dict[str, Any], *, offline: bool = False, no_ai: bool = False,
    require_ai: bool = False, now: datetime | None = None, llm: LLMClient | None = None,
    repository: IntelligenceRepository | None = None,
    market_workflow: MarketWorkflow | None = None,
    intelligence_workflow: IntelligenceWorkflow | None = None,
    publisher: DigestPublisher | None = None,
    github_workflow: Any | None = None,
    experiment_id: str = "default",
    force_analysis: bool = False,
    run_name: str | None = None,
) -> dict[str, Path]:
    timezone = ZoneInfo(settings["app"]["timezone"])
    current = now.astimezone(timezone) if now else datetime.now(timezone)
    install_proxy_fallback()
    repository = repository or SQLiteIntelligenceRepository(
        resolve_path(settings, "intelligence_db")
    )
    if require_ai and (offline or no_ai):
        raise RuntimeError("--require-ai 已启用，但AI密钥不可用或与离线/禁用AI模式冲突")
    market_runner = market_workflow or MarketPipeline(
        settings, resolve_path(settings, "cache_dir"), current, offline
    )
    if intelligence_workflow is None:
        llm_client = llm or OpenAICompatibleLLM(settings["llm"])
        if require_ai and not llm_client.available:
            raise RuntimeError("--require-ai 已启用，但AI密钥不可用或与离线/禁用AI模式冲突")
        intelligence_runner: IntelligenceWorkflow = IntelligencePipeline(
            settings, repository, llm_client
        )
    else:
        intelligence_runner = intelligence_workflow
    digest_publisher = publisher or FileDigestPublisher()
    experiment_id = sanitize_run_identifier(experiment_id)
    run_id = repository.start_run(
        {"offline": offline, "no_ai": no_ai, "require_ai": require_ai,
         "prompt_version": settings["llm"]["prompt_version"],
         "experiment_id": experiment_id, "force_analysis": force_analysis,
         "broad_reading": {
             "enabled": bool((settings.get("broad_reading") or {}).get("enabled")),
             "prompt_version": (settings.get("broad_reading") or {}).get(
                 "prompt_version", ""
             ),
         }}
    )
    resolved_run_name = sanitize_run_identifier(
        run_name or f"{current:%H%M%S}-{run_id:04d}-{experiment_id}"
    )
    run_metadata: dict[str, Any] = {"run_id": run_id}
    try:
        progress(f"[1/6] 运行市场数据… 实验 {experiment_id}")
        market = market_runner.run()
        progress("[2/6] 采集、去重并聚类科技来源…")
        intelligence = intelligence_runner.run(
            current, market.radar_news,
            offline=offline, no_ai=no_ai, require_ai=require_ai,
            experiment_id=experiment_id, force_analysis=force_analysis,
        )
        progress("[3/6] 采集开源热门项目…")
        github_runner = github_workflow or GitHubTrendingPipeline(
            settings, resolve_path(settings, "cache_dir"),
        )
        github = github_runner.run(current)
        progress("[4/6] 汇总分析、质量门与页签数据…")

        ai_label = AI_STATUS_LABELS.get(intelligence.ai_status, intelligence.ai_status)
        briefing_errors: list[str] = []
        stages = getattr(intelligence_runner, "stages", None)
        github_brief_status = "skipped"
        github_briefed_projects = 0
        if github.projects and stages is not None and stages.available and not no_ai:
            for index, project in enumerate(github.projects, start=1):
                try:
                    for attempt in range(3):
                        suffix = " · 补充场景" if attempt else ""
                        progress(
                            "当前：撰写开源项目功能说明 "
                            f"{index}/{len(github.projects)} · "
                            f"{project.get('full_name') or '仓库'}{suffix}"
                        )
                        apply_git_brief(stages.brief_github([project]), [project])
                        if project.get("ai_briefed") and project.get("use_cases"):
                            break
                    if not (project.get("ai_briefed") and project.get("use_cases")):
                        briefing_errors.append(
                            "git_brief "
                            f"{project.get('full_name') or 'unknown'}: "
                            "模型未返回可发布的具体使用场景"
                        )
                except Exception as exc:
                    briefing_errors.append(
                        "git_brief "
                        f"{project.get('full_name') or 'unknown'}: "
                        f"{type(exc).__name__}: {exc}"
                    )
            github_briefed_projects = sum(
                bool(item.get("ai_briefed") and item.get("use_cases"))
                for item in github.projects
            )
            if github_briefed_projects == len(github.projects):
                github_brief_status = "generated"
            elif any(item.get("ai_briefed") for item in github.projects):
                github_brief_status = "partial"
            else:
                github_brief_status = "failed"
        model_runtime = intelligence.model_runtime
        usage = intelligence.usage
        audit_owner = (
            intelligence_runner
            if callable(getattr(intelligence_runner, "runtime_metadata", None))
            else stages
        )
        if audit_owner is not None:
            runtime_metadata = getattr(audit_owner, "runtime_metadata", None)
            if callable(runtime_metadata):
                model_runtime = runtime_metadata()
                model_runtime["analysis_models"] = sorted({
                    item.model
                    for item in intelligence.analyses
                    if item.model and item.model != "none"
                    and "broad_reading_only" not in item.quality.issues
                })
            usage = getattr(audit_owner, "usage", intelligence.usage)
        intensive_limit = max(
            0, int(settings["intelligence"]["intensive_reading_events"])
        )
        intensive_analyses, extensive_analyses = partition_reading_analyses(
            intelligence.analyses, intensive_limit
        )
        context = {
            **market.context,
            "title": settings["app"]["title"],
            "generated_at": current.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "report_date": current.strftime("%Y-%m-%d"),
            "run_name": resolved_run_name,
            "experiment_id": experiment_id,
            "analyses": intelligence.analyses,
            "intensive_analyses": intensive_analyses,
            "extensive_analyses": extensive_analyses,
            "intelligence_source_status": intelligence.source_status,
            "ai_status": intelligence.ai_status,
            "ai_status_label": ai_label,
            "usage": usage,
            "quality_summary": intelligence.quality_summary,
            "model_runtime": model_runtime,
            "prompt_version": settings["llm"]["prompt_version"],
            "pipeline_errors": [*intelligence.errors, *github.errors, *briefing_errors],
            "github_projects": github.projects,
            "github_source_status": github.source_status,
            "github_brief_status": github_brief_status,
            "github_briefed_projects": github_briefed_projects,
        }
        failed_sources = [
            item["name"] for item in market.context["market_source_status"] if item["stale"]
        ] + [item["name"] for item in intelligence.source_status if item["stale"]] + [
            item["name"] for item in github.source_status if item["stale"]
        ]
        run_metadata = {
            **market.metadata,
            "run_id": run_id,
            "generated_at": current.isoformat(timespec="seconds"),
            "offline": offline,
            "experiment_id": experiment_id,
            "run_name": resolved_run_name,
            "force_analysis": force_analysis,
            "ai": {
                "status": intelligence.ai_status,
                **model_runtime,
                "prompt_version": settings["llm"]["prompt_version"],
                "usage": usage,
            },
            "intelligence": {
                "collected_documents": intelligence.collected_documents,
                "clustered_events": intelligence.clustered_events,
                "published_events": len(intelligence.analyses),
                "intensive_reading_events": len(intensive_analyses),
                "extensive_reading_events": len(extensive_analyses),
                "intensive_material_ineligible_events": sum(
                    bool(intensive_material_issue(item))
                    for item in intelligence.analyses
                ),
                "deep_events": sum(item.status.value == "deep" for item in intelligence.analyses),
                "quality": intelligence.quality_summary,
                "processing_funnel": intelligence.processing_funnel,
                "processing_trace": intelligence.processing_trace,
                "cache": {
                    "scope": intelligence.cache_scope,
                    "hits": intelligence.analysis_cache_hits,
                    "misses": intelligence.analysis_cache_misses,
                    "forced": force_analysis,
                },
                "sources": intelligence.source_status,
                "errors": intelligence.errors,
            },
            "github": {
                "published_projects": len(github.projects),
                "brief_status": github_brief_status,
                "briefed_projects": github_briefed_projects,
                "sources": github.source_status,
                "errors": [*github.errors, *briefing_errors],
            },
            "freshness": {
                "failed_or_cached_sources": failed_sources,
                "all_sources_fresh": not failed_sources,
            },
        }
        progress("[5/6] 生成 HTML…")
        outputs = digest_publisher.publish(
            context, intelligence.analyses,
            run_metadata, resolve_path(settings, "output_dir"), current,
        )
        progress("[6/6] 保存运行状态…")
        repository.finish_run(run_id, "success", run_metadata)
        return outputs
    except Exception as exc:
        repository.finish_run(
            run_id, "failed", {**run_metadata, "error": f"{type(exc).__name__}: {exc}"[:2000]},
        )
        raise
