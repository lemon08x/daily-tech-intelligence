from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

import pandas as pd

from daily_intel.core.models import (
    Analysis,
    AnalysisQuality,
    AnalysisStatus,
    Document,
    Event,
    Evidence,
)
from daily_intel.intelligence.extraction import enrich_document
from daily_intel.intelligence.mapping import CompanyMapper
from daily_intel.intelligence.modeling import ModelStageRunner
from daily_intel.intelligence.quality import AnalysisQualityGate
from daily_intel.intelligence.sources.common import event_lane


Enricher = Callable[[Document, int, int], Document]
MapperFactory = Callable[..., CompanyMapper]


class EventResearcher:
    """Researches one event; collection, selection and publication stay outside."""

    def __init__(
        self, settings: dict[str, Any], stages: ModelStageRunner,
        quality_gate: AnalysisQualityGate, enricher: Enricher = enrich_document,
        mapper_factory: MapperFactory = CompanyMapper,
    ) -> None:
        self.settings = settings
        self.config = settings["intelligence"]
        self.stages = stages
        self.quality_gate = quality_gate
        self.enricher = enricher
        self.mapper_factory = mapper_factory

    def can_reuse(self, analysis: Analysis, ai_enabled: bool) -> bool:
        if not ai_enabled:
            return True
        return (
            analysis.model != "none"
            and analysis.prompt_version == self.stages.prompt_version
            and analysis.quality.policy_version == self.quality_gate.policy.version
        )

    def enrich(self, documents: list[Document]) -> tuple[list[Document], list[str]]:
        enriched: list[Document] = []
        errors: list[str] = []
        for document in documents:
            item = self.enricher(
                document,
                int(self.config["source_fetch_timeout_seconds"]),
                int(self.config["full_text_max_chars"]),
            )
            enriched.append(item)
            if item.metadata.get("extraction_error"):
                errors.append(
                    f"extraction {item.source_id}/{item.id}: {item.metadata['extraction_error']}"
                )
        return enriched, errors

    def analyze(
        self, event: Event, documents: list[Document], snapshot: pd.DataFrame, now: datetime,
    ) -> Analysis:
        draft, model = self.stages.analyze(event, documents)
        verification = self.stages.verify(event, documents, draft)
        decision = self.quality_gate.evaluate(draft, verification, documents)
        mappings = []
        if decision.deep:
            mapper = self.mapper_factory(snapshot, now, offline=False)
            mappings = mapper.resolve(decision.draft.company_hypotheses)
        return self.quality_gate.build_analysis(
            event.id, decision, mappings, model, self.stages.prompt_version, now,
            lane=event_lane(documents),
        )

    def lead(
        self, event: Event, documents: list[Document], now: datetime, reason: str,
    ) -> Analysis:
        evidence = [
            Evidence(
                document_id=document.id,
                url=document.url,
                quote=(document.summary or document.title)[:800],
                locator="来源摘要",
            )
            for document in documents
            if len(document.summary or document.title) >= 8
        ][:3]
        return Analysis(
            event_id=event.id,
            status=AnalysisStatus.LEAD,
            headline=event.title,
            plain_takeaway=reason,
            key_facts=[reason] + [document.summary[:300] for document in documents[:2] if document.summary],
            risks=["尚未完成强模型深研与独立证据校验"],
            confidence=.3,
            evidence=evidence,
            quality=AnalysisQuality(
                policy_version=self.quality_gate.policy.version,
                passed=False,
                score=30,
                supported_evidence=len(evidence),
                primary_sources=len({item.source_id for item in documents if item.source_tier == 1}),
                source_diversity=len({item.source_id for item in documents}),
                issues=["ai_not_enabled"],
            ),
            model="none",
            prompt_version=self.stages.prompt_version,
            created_at=now,
            lane=event_lane(documents),
        )
