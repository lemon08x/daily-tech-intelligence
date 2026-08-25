from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from daily_intel.core.ports import LLMClient
from daily_intel.core.settings import resolve_path
from daily_intel.infrastructure.llm.openai_compatible import OpenAICompatibleLLM
from daily_intel.infrastructure.storage.sqlite import SQLiteIntelligenceRepository
from daily_intel.intelligence.pipeline import IntelligencePipeline
from daily_intel.market.pipeline import MarketPipeline
from daily_intel.publication.reporting import publish


AI_STATUS_LABELS = {
    "enabled": "AI深研已启用",
    "disabled": "AI未启用，展示权威来源线索",
    "cached": "离线复用已存分析",
    "unavailable": "离线且没有已存分析",
}


def run_application(
    settings: dict[str, Any], *, offline: bool = False, no_ai: bool = False,
    require_ai: bool = False, now: datetime | None = None, llm: LLMClient | None = None,
) -> dict[str, Path]:
    timezone = ZoneInfo(settings["app"]["timezone"])
    current = now.astimezone(timezone) if now else datetime.now(timezone)
    repository = SQLiteIntelligenceRepository(resolve_path(settings, "intelligence_db"))
    llm_client = llm or OpenAICompatibleLLM(settings["llm"])
    if require_ai and (offline or no_ai or not llm_client.available):
        raise RuntimeError("--require-ai 已启用，但AI密钥不可用或与离线/禁用AI模式冲突")
    run_id = repository.start_run(
        {"offline": offline, "no_ai": no_ai, "require_ai": require_ai,
         "prompt_version": settings["llm"]["prompt_version"]}
    )
    run_metadata: dict[str, Any] = {"run_id": run_id}
    try:
        print("[1/5] 运行A股市场数据与规则评分…", flush=True)
        market = MarketPipeline(
            settings, resolve_path(settings, "cache_dir"), current, offline
        ).run()
        print("[2/5] 采集、去重并聚类权威科技来源…", flush=True)
        intelligence = IntelligencePipeline(settings, repository, llm_client).run(
            current, market.snapshot, market.radar_news,
            offline=offline, no_ai=no_ai, require_ai=require_ai,
        )
        print("[3/5] 整理AI深研、证据校验与产业映射…", flush=True)

        ai_label = AI_STATUS_LABELS.get(intelligence.ai_status, intelligence.ai_status)
        context = {
            **market.context,
            "title": settings["app"]["title"],
            "generated_at": current.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "report_date": current.strftime("%Y-%m-%d"),
            "analyses": intelligence.analyses,
            "intelligence_source_status": intelligence.source_status,
            "ai_status": intelligence.ai_status,
            "ai_status_label": ai_label,
            "usage": intelligence.usage,
            "prompt_version": settings["llm"]["prompt_version"],
            "pipeline_errors": intelligence.errors,
        }
        failed_sources = [
            item["name"] for item in market.context["market_source_status"] if item["stale"]
        ] + [item["name"] for item in intelligence.source_status if item["stale"]]
        run_metadata = {
            **market.metadata,
            "run_id": run_id,
            "generated_at": current.isoformat(timespec="seconds"),
            "offline": offline,
            "ai": {
                "status": intelligence.ai_status,
                "provider": settings["llm"]["provider"],
                "base_url": settings["llm"]["base_url"],
                "api_key_env": settings["llm"]["api_key_env"],
                "models": {
                    stage: settings["llm"][stage]["model"]
                    for stage in ("scout", "analyst", "verifier")
                },
                "prompt_version": settings["llm"]["prompt_version"],
                "usage": intelligence.usage,
            },
            "intelligence": {
                "collected_documents": intelligence.collected_documents,
                "clustered_events": intelligence.clustered_events,
                "published_events": len(intelligence.analyses),
                "deep_events": sum(item.status.value == "deep" for item in intelligence.analyses),
                "sources": intelligence.source_status,
                "errors": intelligence.errors,
            },
            "freshness": {
                "failed_or_cached_sources": failed_sources,
                "all_sources_fresh": not failed_sources,
            },
        }
        print("[4/5] 生成统一HTML、Markdown和可追溯数据文件…", flush=True)
        outputs = publish(
            context, intelligence.analyses, market.snapshot, market.candidates,
            run_metadata, resolve_path(settings, "output_dir"), current,
        )
        print("[5/5] 保存流水线状态…", flush=True)
        repository.finish_run(run_id, "success", run_metadata)
        return outputs
    except Exception as exc:
        repository.finish_run(
            run_id, "failed", {**run_metadata, "error": f"{type(exc).__name__}: {exc}"[:2000]},
        )
        raise
