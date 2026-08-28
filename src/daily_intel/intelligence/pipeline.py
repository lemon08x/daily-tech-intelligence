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
    errors: list[str] = field(default_factory=list)
    process: dict[str, Any] = field(default_factory=dict)


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
        progress(f"当前：选题完成，准备深研 {len(selected)} 个事件")

        analyses: list[Analysis] = []
        cache_hits = 0
        cache_misses = 0
        max_general = int(self.config.get("max_general_events", 5))
        max_hardcore = int(self.config.get("max_hardcore_events", 5))
        overall = int(self.config.get("max_deep_events", max_general + max_hardcore))
        lane_counts = {"general": 0, "hardcore": 0}
        research_rows: list[dict[str, Any]] = []
        for event, event_documents in selected:
            lane = event_lane(event_documents)
            lane_limit = max_general if lane == "general" else max_hardcore
            if lane_counts[lane] >= lane_limit or len(analyses) >= overall:
                research_rows.append(_research_row(
                    event, event_documents, lane, "lane_cap",
                    reason=f"{lane} 已满 {lane_limit} 或总数已满 {overall}",
                ))
                if lane_counts["general"] >= max_general and lane_counts["hardcore"] >= max_hardcore:
                    break
                if len(analyses) >= overall:
                    break
                continue
            cached = (
                None
                if force_analysis
                else self.repository.get_analysis(event.id, cache_scope)
            )
            title = (event.title or event.id)[:48]
            if cached and self.researcher.can_reuse(cached, ai_enabled):
                progress(f"当前：复用缓存 · {title}")
                cache_hits += 1
                cached = cached.model_copy(update={"lane": lane})
                analyses.append(cached)
                lane_counts[lane] += 1
                research_rows.append(_research_row(
                    event, event_documents, lane, "cache", analysis=cached,
                ))
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
                    analysis = self.researcher.analyze(event, enriched, snapshot, now)
                except Exception as exc:
                    errors.append(f"{event.id}: {type(exc).__name__}: {exc}")
                    research_rows.append(_research_row(
                        event, event_documents, lane, "failed",
                        reason=f"{type(exc).__name__}: {exc}",
                    ))
                    continue
            self.repository.save_analysis(analysis, cache_scope)
            analyses.append(analysis)
            lane_counts[analysis.lane] += 1
            research_rows.append(_research_row(
                event, event_documents, lane, "analyzed" if ai_enabled else "lead",
                analysis=analysis,
            ))

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
            process=_intelligence_process(
                documents, event_docs, self.selector.last_trace, research_rows,
                cache_scope, max_general, max_hardcore, overall,
            ),
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
        process: dict[str, Any] | None = None,
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
            process=process or {},
        )


def _doc_row(document: Document) -> dict[str, Any]:
    return {
        "id": document.id,
        "title": document.title,
        "source_id": document.source_id,
        "source_name": document.source_name,
        "url": document.url,
        "tier": document.source_tier,
        "lane": str((document.metadata or {}).get("lane") or ""),
        "content_type": document.content_type,
        "published_at": document.published_at.isoformat(timespec="seconds") if document.published_at else "",
    }


def _event_row(event: Event, documents: list[Document]) -> dict[str, Any]:
    return {
        "event_id": event.id,
        "title": event.title,
        "topic": event.topic_name or event.topic_id,
        "lane": event_lane(documents),
        "deterministic_score": round(float(event.deterministic_score), 2),
        "document_count": len(documents),
        "documents": [item.title for item in documents],
        "sources": [item.source_name for item in documents],
    }


def _research_row(
    event: Event,
    documents: list[Document],
    lane: str,
    decision: str,
    *,
    analysis: Analysis | None = None,
    reason: str = "",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "event_id": event.id,
        "title": event.title,
        "lane": lane,
        "decision": decision,
        "reason": reason,
        "sources": [item.source_name for item in documents],
        "urls": [item.url for item in documents if item.url],
    }
    if analysis is not None:
        row.update({
            "headline": analysis.headline,
            "status": analysis.status.value,
            "confidence": analysis.confidence,
            "quality_score": analysis.quality.score,
            "quality_passed": analysis.quality.passed,
            "issues": list(analysis.quality.issues),
            "unsupported_claims": list(analysis.quality.unsupported_claims),
            "evidence_count": analysis.quality.supported_evidence,
            "primary_sources": analysis.quality.primary_sources,
            "plain_takeaway": analysis.plain_takeaway,
        })
    return row


def _intelligence_process(
    documents: list[Document],
    event_docs: list[tuple[Event, list[Document]]],
    selection: list[dict[str, Any]],
    research: list[dict[str, Any]],
    cache_scope: str,
    max_general: int,
    max_hardcore: int,
    overall: int,
) -> dict[str, Any]:
    return {
        "documents": [_doc_row(item) for item in documents],
        "events": [_event_row(event, docs) for event, docs in event_docs],
        "selection": list(selection or []),
        "research": research,
        "cache_scope": cache_scope,
        "limits": {
            "max_general": max_general,
            "max_hardcore": max_hardcore,
            "max_deep": overall,
        },
    }
