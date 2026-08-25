from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from daily_intel.core.models import Analysis, Document
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
from daily_intel.intelligence.research import EventResearcher
from daily_intel.intelligence.selection import EventSelector


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

    def run(
        self,
        now: datetime,
        snapshot: pd.DataFrame,
        radar_news: pd.DataFrame,
        offline: bool = False,
        no_ai: bool = False,
        require_ai: bool = False,
        experiment_id: str = "default",
        force_analysis: bool = False,
    ) -> IntelligenceRunResult:
        self.stages.begin_run()
        if require_ai and (offline or no_ai or not self.stages.available):
            raise RuntimeError("--require-ai 已启用，但AI密钥不可用或与离线/禁用AI模式冲突")
        ai_enabled = self.stages.available and not no_ai and not offline
        cache_experiment = (
            experiment_id if ai_enabled or offline else f"{experiment_id}-no-ai"
        )
        cache_scope = self.stages.cache_scope(cache_experiment)
        if offline:
            return self._offline_result(cache_scope)

        documents, source_status = self._collect_sources(now)
        documents.extend(self.collector.radar_documents(radar_news, now))
        event_docs = self.catalog.index_and_discover(documents, now)

        errors: list[str] = []
        if ai_enabled and event_docs:
            selected, scout_error = self.selector.select(event_docs, cache_scope)
            if scout_error:
                errors.append(scout_error)
        else:
            selected = self.selector.balance(event_docs)

        analyses: list[Analysis] = []
        cache_hits = 0
        cache_misses = 0
        target_count = int(self.config["max_deep_events"])
        for event, event_documents in selected:
            if len(analyses) >= target_count:
                break
            cached = (
                None
                if force_analysis
                else self.repository.get_analysis(event.id, cache_scope)
            )
            if cached and self.researcher.can_reuse(cached, ai_enabled):
                cache_hits += 1
                analyses.append(cached)
                continue
            cache_misses += 1
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
                    analysis = self.researcher.analyze(event, enriched, snapshot, now)
                except Exception as exc:
                    errors.append(f"{event.id}: {type(exc).__name__}: {exc}")
                    # A malformed or failed model response is skipped, never synthesized.
                    continue
            self.repository.save_analysis(analysis, cache_scope)
            analyses.append(analysis)

        return self._result(
            analyses=analyses,
            source_status=source_status,
            ai_status="enabled" if ai_enabled else "disabled",
            collected_documents=len(documents),
            clustered_events=len(event_docs),
            cache_scope=cache_scope,
            analysis_cache_hits=cache_hits,
            analysis_cache_misses=cache_misses,
            errors=errors,
        )

    def _offline_result(self, cache_scope: str) -> IntelligenceRunResult:
        limit = int(self.config["max_deep_events"])
        analyses = [
            item
            for item in self.repository.get_latest_analyses(limit * 4)
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
        errors: list[str] | None = None,
    ) -> IntelligenceRunResult:
        model_runtime = self.stages.runtime_metadata()
        model_runtime["analysis_models"] = sorted({
            item.model for item in analyses if item.model and item.model != "none"
        })
        return IntelligenceRunResult(
            analyses=analyses,
            source_status=source_status,
            ai_status=ai_status,
            usage=self.stages.usage,
            model_runtime=model_runtime,
            quality_summary=summarize_quality(analyses),
            collected_documents=collected_documents,
            clustered_events=clustered_events,
            cache_scope=cache_scope,
            analysis_cache_hits=analysis_cache_hits,
            analysis_cache_misses=analysis_cache_misses,
            errors=errors or [],
        )

    def _collect_sources(
        self, now: datetime
    ) -> tuple[list[Document], list[dict[str, Any]]]:
        """Compatibility proxy retained for existing tests and local integrations."""
        return self.collector.collect_sources(now)
