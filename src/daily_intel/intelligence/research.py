from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from daily_intel.core.models import (
    Analysis,
    AnalysisQuality,
    AnalysisStatus,
    Document,
    Event,
    Evidence,
)
from daily_intel.intelligence.extraction import enrich_document
from daily_intel.intelligence.modeling import ModelStageRunner
from daily_intel.intelligence.quality import AnalysisQualityGate, exact_evidence_quote
from daily_intel.intelligence.sources.common import event_lane


Enricher = Callable[[Document, int, int], Document]


class EventResearcher:
    """Researches one event; collection, selection and publication stay outside."""

    def __init__(
        self, settings: dict[str, Any], stages: ModelStageRunner,
        quality_gate: AnalysisQualityGate, enricher: Enricher = enrich_document,
    ) -> None:
        self.settings = settings
        self.config = settings["intelligence"]
        self.stages = stages
        self.quality_gate = quality_gate
        self.enricher = enricher

    def can_reuse(self, analysis: Analysis, ai_enabled: bool) -> bool:
        if not ai_enabled:
            return True
        return (
            analysis.model != "none"
            and analysis.prompt_version == self.stages.prompt_version
            and analysis.quality.policy_version == self.quality_gate.policy.version
        )

    def prepare_cached(
        self, analysis: Analysis, documents: list[Document], ai_enabled: bool,
    ) -> Analysis | None:
        """Make cached quotes exact source spans or reject unsafe cache reuse."""
        if not self.can_reuse(analysis, ai_enabled):
            return None
        documents_by_id = {item.id: item for item in documents}
        normalized: list[Evidence] = []
        for evidence in analysis.evidence:
            document = documents_by_id.get(evidence.document_id)
            if document is None or evidence.url.rstrip("/") != document.url.rstrip("/"):
                return None
            exact_quote = exact_evidence_quote(evidence.quote, document)
            if exact_quote is None:
                return None
            normalized.append(evidence.model_copy(update={"quote": exact_quote}))
        return analysis.model_copy(update={"evidence": normalized})

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
        self, event: Event, documents: list[Document], now: datetime,
    ) -> Analysis:
        draft, model = self.stages.analyze(event, documents)
        verification = self.stages.verify(event, documents, draft)
        decision = self.quality_gate.evaluate(draft, verification, documents)
        return self.quality_gate.build_analysis(
            event.id, decision, model, self.stages.prompt_version, now,
            lane=event_lane(documents),
        )

    def lead(
        self,
        event: Event,
        documents: list[Document],
        now: datetime,
        reason: str,
        *,
        model: str = "none",
        prompt_version: str | None = None,
        issue: str = "ai_not_enabled",
        selection_reason: str = "",
    ) -> Analysis:
        evidence = [
            Evidence(
                document_id=document.id,
                url=document.url,
                quote=(document.content or document.summary or document.title)[:800],
                locator=(
                    "来源正文" if document.content else
                    "来源摘要" if document.summary else "来源标题"
                ),
            )
            for document in documents
            if len(document.content or document.summary or document.title) >= 8
        ][:3]
        broad_only = issue == "broad_reading_only"
        facts = [reason]
        if selection_reason and selection_reason.strip() != reason.strip():
            facts.append("入选理由：" + selection_reason.strip())
        facts.extend(
            document.summary[:300]
            for document in documents[:2]
            if document.summary and document.summary.strip() != reason.strip()
        )
        return Analysis(
            event_id=event.id,
            status=AnalysisStatus.LEAD,
            headline=event.title,
            plain_takeaway=reason,
            key_facts=facts[:8],
            risks=[
                "仅完成批量泛读与 DeepSeek 统一复排，尚未经过 Analyst、Verifier 和确定性质量门"
                if broad_only else
                "尚未完成强模型深研与独立证据校验"
            ],
            confidence=.3,
            evidence=evidence,
            quality=AnalysisQuality(
                policy_version=self.quality_gate.policy.version,
                passed=False,
                score=30,
                supported_evidence=len(evidence),
                primary_sources=len({item.source_id for item in documents if item.source_tier == 1}),
                source_diversity=len({item.source_id for item in documents}),
                issues=[issue],
            ),
            model=model,
            prompt_version=prompt_version or self.stages.prompt_version,
            created_at=now,
            lane=event_lane(documents),
        )
