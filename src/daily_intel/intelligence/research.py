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
    PublicationMeta,
    ResearchStatus,
)
from daily_intel.intelligence.extraction import enrich_document
from daily_intel.intelligence.claims import extract_claims
from daily_intel.intelligence.modeling import ModelStageRunner
from daily_intel.intelligence.quality import AnalysisQualityGate, exact_evidence_quote
from daily_intel.intelligence.snapshots import (
    content_fingerprint,
    documents_snapshot_hash,
    stage_documents, snapshot_text,
)
from daily_intel.intelligence.sources.common import event_lane
from daily_intel.intelligence.terms import localize_public_text


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

    def _stage_limit(self) -> int:
        return int(self.config.get("full_text_max_chars", 50000))

    def can_reuse(self, analysis: Analysis, ai_enabled: bool) -> bool:
        if not ai_enabled:
            return True
        quality = analysis.quality
        return (
            analysis.model != "none"
            and analysis.prompt_version == self.stages.prompt_version
            and quality.policy_version == self.quality_gate.policy.version
            and (quality.schema_version or "") == self.quality_gate.policy.schema_version
            and bool(quality.claim_audits)
        )

    def prepare_cached(
        self, analysis: Analysis, documents: list[Document], ai_enabled: bool,
    ) -> Analysis | None:
        """Make cached quotes exact source spans or reject unsafe cache reuse."""
        if not self.can_reuse(analysis, ai_enabled):
            return None
        clipped = stage_documents(documents, self._stage_limit())
        current_hash = documents_snapshot_hash(clipped)
        stored_hash = analysis.publication.snapshot_hash or ""
        if not stored_hash or stored_hash != current_hash:
            return None
        if not self.stages.repository.get_content_snapshot(stored_hash):
            return None
        documents_by_id = {item.id: item for item in clipped}
        normalized: list[Evidence] = []
        for evidence in analysis.evidence:
            document = documents_by_id.get(evidence.document_id)
            if document is None or evidence.url.rstrip("/") != document.url.rstrip("/"):
                return None
            exact_quote = exact_evidence_quote(evidence.quote, document)
            if exact_quote is None:
                return None
            normalized.append(evidence.model_copy(update={"quote": exact_quote}))
        grounded = self.quality_gate.ground_claims(analysis.quality.claim_audits, clipped)
        if any(before.status != after.status for before, after in zip(analysis.quality.claim_audits, grounded)):
            return None
        if analysis.quality.passed and (not grounded or any(item.status.value != "supported" for item in grounded)):
            return None
        fingerprint = content_fingerprint(clipped)
        publication = analysis.publication.model_copy(update={
            "cache_hit": True,
            "snapshot_hash": current_hash or stored_hash,
            "content_fingerprint": analysis.publication.content_fingerprint or fingerprint,
        })
        return analysis.model_copy(update={"evidence": normalized, "publication": publication})

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
        clipped = stage_documents(documents, self._stage_limit())
        self.stages.repository.save_content_snapshot(snapshot_text(clipped))
        draft, model = self.stages.analyze(event, clipped)
        draft = self.quality_gate.normalize_draft(draft)
        claims = extract_claims(draft)
        verification = self.stages.verify(event, clipped, draft, claims=claims)
        decision = self.quality_gate.evaluate(draft, verification, clipped, claim_inventory=claims)
        analysis = self.quality_gate.build_analysis(
            event.id, decision, model, self.stages.prompt_version, now,
            lane=event_lane(documents),
            topic_id=event.topic_id, topic_name=event.topic_name,
            source_headline=event.title,
        )
        publication = analysis.publication.model_copy(update={
            "content_fingerprint": content_fingerprint(clipped),
            "snapshot_hash": decision.snapshot_hash,
            "was_research_target": True,
            "cache_hit": False,
        })
        return analysis.model_copy(update={"publication": publication})

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
        headline: str = "",
        summary: str = "",
        facts: list[str] | None = None,
        evidence: list[Evidence] | None = None,
        research_status: str = "",
    ) -> Analysis:
        clipped = stage_documents(documents, self._stage_limit())
        # No unrelated first-three-document fallback: evidence must come from public facts.
        lead_evidence = [item for item in (evidence or []) if any(
            d.id == item.document_id and d.url == item.url
            and item.quote in (d.content or d.summary) for d in clipped
        )]
        broad_only = issue == "broad_reading_only"
        public_summary = localize_public_text(summary or reason)
        public_facts = [localize_public_text(item) for item in (facts or []) if item.strip()]
        if selection_reason and selection_reason.strip() not in public_facts:
            # Keep editorial reason in audit-only risks, not public facts.
            pass
        excerpts = [
            (document.summary or document.title).strip()
            for document in documents[:2]
            if (document.summary or document.title).strip()
        ]
        status = research_status or (
            ResearchStatus.BROAD_ONLY.value if broad_only or issue == "ai_not_enabled" else ResearchStatus.RESEARCH_FAILED.value
        )
        return Analysis(
            event_id=event.id,
            status=AnalysisStatus.LEAD,
            headline=localize_public_text(headline or event.title),
            scan_sentence=public_summary[:220],
            plain_takeaway=public_summary,
            key_facts=public_facts[:8],
            risks=[
                "仅完成批量泛读与 DeepSeek 统一复排，尚未经过 Analyst、Verifier 和确定性质量门"
                if broad_only else
                "尚未完成强模型深研与独立证据校验"
            ],
            confidence=.3,
            evidence=lead_evidence,
            quality=AnalysisQuality(
                policy_version=self.quality_gate.policy.version,
                passed=False,
                score=30,
                supported_evidence=len(lead_evidence),
                primary_sources=len({item.source_id for item in documents if item.source_tier == 1}),
                source_diversity=len({item.source_id for item in documents}),
                issues=[issue],
                schema_version=self.quality_gate.policy.schema_version,
            ),
            model=model,
            prompt_version=prompt_version or self.stages.prompt_version,
            created_at=now,
            lane=event_lane(documents),
            topic_id=event.topic_id,
            topic_name=event.topic_name,
            publication=PublicationMeta(
                source_headline=event.title,
                research_status=status,
                content_fingerprint=content_fingerprint(clipped),
                snapshot_hash=documents_snapshot_hash(clipped),
                source_excerpts=excerpts,
                schema_version=self.quality_gate.policy.schema_version,
            ),
        )
