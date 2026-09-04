from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from daily_intel.core.models import Analysis, Document, Event
from daily_intel.core.progress import progress
from daily_intel.core.ports import IntelligenceRepository, LLMClient
from daily_intel.intelligence.clustering import is_obvious_build_title
from daily_intel.intelligence.collection import DocumentCollector
from daily_intel.intelligence.discovery import EventCatalog
from daily_intel.intelligence.modeling import ModelStageRunner
from daily_intel.intelligence.quality import (
    AnalysisQualityGate,
    QualityPolicy,
    summarize_quality,
)
from daily_intel.intelligence.reading import (
    intensive_material_issue,
    partition_reading_analyses,
)
from daily_intel.intelligence.research import EventResearcher
from daily_intel.intelligence.selection import EventSelector
from daily_intel.intelligence.sources.common import event_lane


@dataclass(slots=True)
class IntelligenceRunResult:
    analyses: list[Analysis]
    source_status: list[dict[str, Any]]
    ai_status: str
    usage: dict[str, Any]
    model_runtime: dict[str, Any] = field(default_factory=dict)
    quality_summary: dict[str, Any] = field(default_factory=dict)
    collected_documents: int = 0
    clustered_events: int = 0
    cache_scope: str = ""
    analysis_cache_hits: int = 0
    analysis_cache_misses: int = 0
    processing_funnel: dict[str, int] = field(default_factory=dict)
    processing_trace: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class IntelligencePipeline:
    """Thin application service that composes replaceable intelligence stages."""

    def __init__(
        self,
        settings: dict[str, Any],
        repository: IntelligenceRepository,
        llm: LLMClient,
        *,
        collector: DocumentCollector | None = None,
        catalog: EventCatalog | None = None,
        stages: ModelStageRunner | None = None,
        selector: EventSelector | None = None,
        researcher: EventResearcher | None = None,
    ) -> None:
        self.settings = settings
        self.config = settings["intelligence"]
        self.repository = repository
        policy = QualityPolicy.from_settings(settings)
        self.collector = collector or DocumentCollector(settings, repository)
        self.catalog = catalog or EventCatalog(settings, repository)
        self.stages = stages or ModelStageRunner(settings, repository, llm, policy)
        self.selector = selector or EventSelector(settings, repository, self.stages)
        self.researcher = researcher or EventResearcher(
            settings, self.stages, AnalysisQualityGate(policy)
        )

    @property
    def usage(self) -> dict[str, Any]:
        return self.stages.usage

    def runtime_metadata(self) -> dict[str, Any]:
        metadata = self.stages.runtime_metadata()
        metadata["broad_reading"] = {
            "enabled": bool(self.selector.broad_reading_enabled),
            "mode": "single_model_scan_shortlist_then_rerank",
            "prompt_version": self.selector.broad_prompt_version,
            "shortlist_events": self.selector.shortlist_events,
            "primary": self._runner_summary(self.stages),
        }
        return metadata

    def run(
        self,
        now: datetime,
        radar_news: pd.DataFrame,
        offline: bool = False,
        no_ai: bool = False,
        require_ai: bool = False,
        experiment_id: str = "default",
        force_analysis: bool = False,
    ) -> IntelligenceRunResult:
        self.stages.begin_run()
        self.selector.begin_run()
        if require_ai and (offline or no_ai or not self.stages.available):
            raise RuntimeError("--require-ai 已启用，但AI密钥不可用或与离线/禁用AI模式冲突")
        ai_enabled = self.stages.available and not no_ai and not offline
        cache_experiment = (
            experiment_id if ai_enabled or offline else f"{experiment_id}-no-ai"
        )
        cache_scope = self.stages.cache_scope(cache_experiment)
        if offline:
            return self._offline_result(cache_scope)

        progress("当前：正在采集科技来源…")
        documents, source_status = self.collector.collect_sources(now)
        documents.extend(self.collector.radar_documents(radar_news, now))
        progress(f"当前：采集完成 {len(documents)} 篇，正在聚类…")
        event_docs = self.catalog.index_and_discover(documents, now)

        errors: list[str] = []
        if ai_enabled and event_docs:
            progress(f"当前：正在初筛选题（{len(event_docs)} 个事件）…")
            selected, scout_error = self.selector.select(event_docs, cache_scope, now)
            if scout_error:
                errors.append(scout_error)
        else:
            selected = self.selector.order_with_repeat(event_docs, now)
        selected = self.selector.prioritize_for_research(
            selected,
            int(self.config.get("preferred_general_events", 5)),
            int(self.config.get("preferred_hardcore_events", 5)),
            int(self.config.get("preferred_max_per_topic", 2)),
            self.selector.priority_event_ids,
        )
        intensive_limit = max(0, int(self.config["intensive_reading_events"]))
        bounded_research = ai_enabled and self.selector.broad_reading_enabled
        research_targets = selected[:intensive_limit] if bounded_research else selected
        broad_only_targets = selected[len(research_targets):] if bounded_research else []
        if bounded_research:
            progress(
                f"当前：选题完成；{len(selected)} 条进入发布候选，"
                f"仅前 {len(research_targets)} 条执行 DeepSeek 精读"
            )
        else:
            progress(f"当前：选题完成，准备深研 {len(research_targets)} 个事件")

        analyses: list[Analysis] = []
        cache_hits = 0
        cache_misses = 0
        research_attempted = 0
        research_outcomes: dict[str, tuple[str, str]] = {}
        for event, event_documents in research_targets:
            lane = event_lane(event_documents)
            research_attempted += 1
            cached = (
                None
                if force_analysis
                else self.repository.get_analysis(event.id, cache_scope)
            )
            reusable = (
                self.researcher.prepare_cached(cached, event_documents, ai_enabled)
                if cached is not None else None
            )
            title = (event.title or event.id)[:48]
            if reusable is not None:
                progress(f"当前：复用缓存 · {title}")
                cache_hits += 1
                cached = reusable.model_copy(update={"lane": lane})
                analyses.append(cached)
                research_outcomes[event.id] = (
                    "published", f"复用缓存，质量状态 {cached.status.value}"
                )
                continue
            cache_misses += 1
            progress(f"当前：深研 {lane} · {title}")
            if not ai_enabled:
                analysis = self.researcher.lead(
                    event, event_documents, now, "AI未启用，仅展示权威来源线索"
                )
            else:
                try:
                    enriched, extraction_errors = self.researcher.enrich(event_documents)
                    errors.extend(extraction_errors)
                    for document in enriched:
                        self.repository.update_document_content(document)
                    analysis = self.researcher.analyze(event, enriched, now)
                except Exception as exc:
                    errors.append(f"{event.id}: {type(exc).__name__}: {exc}")
                    if bounded_research:
                        analysis = self._broad_lead(
                            event, event_documents, now,
                            failure=f"精读失败：{type(exc).__name__}: {exc}",
                        )
                        analyses.append(analysis)
                        research_outcomes[event.id] = (
                            "published",
                            f"精读失败，保留泛读摘要：{type(exc).__name__}: {exc}"[:500],
                        )
                        continue
                    research_outcomes[event.id] = (
                        "analysis_failed", f"深研失败：{type(exc).__name__}: {exc}"[:500]
                    )
                    continue
            self.repository.save_analysis(analysis, cache_scope)
            analyses.append(analysis)
            research_outcomes[event.id] = (
                "published", f"完成深研，质量状态 {analysis.status.value}"
            )

        for event, event_documents in broad_only_targets:
            analysis = self._broad_lead(event, event_documents, now)
            analyses.append(analysis)
            research_outcomes[event.id] = (
                "published",
                f"完成 DeepSeek 泛读与复排，未进入前 {intensive_limit} 精读",
            )

        intensive_analyses, extensive_analyses = partition_reading_analyses(
            analyses, intensive_limit
        )
        intensive_ids = {item.event_id for item in intensive_analyses}
        analyses_by_id = {item.event_id: item for item in analyses}
        publication_tiers = {
            analysis.event_id: (
                "intensive" if analysis.event_id in intensive_ids else "extensive"
            )
            for analysis in analyses
        }
        processing_trace = list(getattr(self.catalog, "last_trace", []))
        selection_order = {
            event.id: index for index, (event, _) in enumerate(selected, start=1)
        }
        for item in getattr(self.selector, "last_trace", []):
            traced = dict(item)
            if traced.get("event_id") in selection_order:
                traced["selection_order"] = selection_order[traced["event_id"]]
            outcome = research_outcomes.get(str(traced.get("event_id") or ""))
            if outcome:
                traced["status"], traced["reason"] = outcome
                publication_tier = publication_tiers.get(str(traced.get("event_id") or ""))
                if publication_tier:
                    traced["publication_tier"] = publication_tier
                    label = "精读" if publication_tier == "intensive" else "泛读"
                    material_issue = intensive_material_issue(
                        analyses_by_id[str(traced.get("event_id") or "")]
                    )
                    if material_issue and publication_tier == "extensive":
                        traced["reason"] += f"，精读材料门未通过（{material_issue}），展示为泛读"
                    else:
                        traced["reason"] += f"，展示为{label}"
            elif traced.get("status") == "candidate":
                traced["status"] = "not_researched"
                traced["reason"] = "Scout 保留，但本轮未完成研究；" + str(
                    traced.get("reason") or ""
                )
            processing_trace.append(traced)
        processing_funnel = {
            **getattr(self.catalog, "last_funnel", {}),
            **getattr(self.selector, "last_funnel", {}),
            "research_target_events": len(research_targets),
            "research_attempted_events": research_attempted,
            "broad_only_events": sum(
                "broad_reading_only" in item.quality.issues for item in analyses
            ),
            "published_events": len(analyses),
            "intensive_reading_events": len(intensive_analyses),
            "extensive_reading_events": len(extensive_analyses),
            "intensive_material_ineligible_events": sum(
                bool(intensive_material_issue(item)) for item in analyses
            ),
            "deep_events": sum(item.status.value == "deep" for item in analyses),
        }

        return self._result(
            analyses=analyses,
            source_status=source_status,
            ai_status="enabled" if ai_enabled else "disabled",
            collected_documents=len(documents),
            clustered_events=len(event_docs),
            cache_scope=cache_scope,
            analysis_cache_hits=cache_hits,
            analysis_cache_misses=cache_misses,
            processing_funnel=processing_funnel,
            processing_trace=processing_trace,
            errors=errors,
        )

    def _offline_result(self, cache_scope: str) -> IntelligenceRunResult:
        limit = max(1, int(self.config["offline_analysis_events"]))
        analyses = [
            item
            for item in self.repository.get_latest_analyses(limit)
            if not is_obvious_build_title(item.headline)
        ][:limit]
        return self._result(
            analyses=analyses,
            source_status=[{
                "name": "technology_intelligence",
                "stale": True,
                "error": "离线模式",
                "count": 0,
            }],
            ai_status="cached" if analyses else "unavailable",
            cache_scope=cache_scope,
        )

    def _result(
        self,
        *,
        analyses: list[Analysis],
        source_status: list[dict[str, Any]],
        ai_status: str,
        collected_documents: int = 0,
        clustered_events: int = 0,
        cache_scope: str = "",
        analysis_cache_hits: int = 0,
        analysis_cache_misses: int = 0,
        processing_funnel: dict[str, int] | None = None,
        processing_trace: list[dict[str, Any]] | None = None,
        errors: list[str] | None = None,
    ) -> IntelligenceRunResult:
        model_runtime = self.runtime_metadata()
        model_runtime["analysis_models"] = sorted({
            item.model for item in analyses
            if item.model and item.model != "none"
            and "broad_reading_only" not in item.quality.issues
        })
        return IntelligenceRunResult(
            analyses=analyses,
            source_status=source_status,
            ai_status=ai_status,
            usage=self.usage,
            model_runtime=model_runtime,
            quality_summary=summarize_quality(analyses),
            collected_documents=collected_documents,
            clustered_events=clustered_events,
            cache_scope=cache_scope,
            analysis_cache_hits=analysis_cache_hits,
            analysis_cache_misses=analysis_cache_misses,
            processing_funnel=processing_funnel or {},
            processing_trace=processing_trace or [],
            errors=errors or [],
        )

    def _broad_lead(
        self,
        event: Event,
        documents: list[Document],
        now: datetime,
        *,
        failure: str = "",
    ) -> Analysis:
        details = self.selector.scan_details(event.id)
        scan = str(details.get("scan") or "").strip()
        if not scan:
            scan = next(
                (
                    (document.summary or document.title).strip()
                    for document in documents
                    if (document.summary or document.title).strip()
                ),
                event.title,
            )[:220]
        selection_reason = str(details.get("reason") or "").strip()
        if failure:
            selection_reason = f"{failure}；{selection_reason}".strip("；")
        return self.researcher.lead(
            event, documents, now, scan,
            model=str(details.get("model") or self.stages.configured_model("scout")),
            prompt_version=self.selector.broad_prompt_version,
            issue="broad_reading_only",
            selection_reason=selection_reason,
        )

    @staticmethod
    def _runner_summary(runner: ModelStageRunner) -> dict[str, Any]:
        metadata = runner.runtime_metadata()
        return {
            "provider": metadata.get("provider", type(runner.llm).__name__),
            "base_url": metadata.get("base_url", ""),
            "configured_model": runner.configured_model("scout"),
            "actual_models": metadata.get("models", {}),
        }
