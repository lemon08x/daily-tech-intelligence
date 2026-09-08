from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from daily_intel.core.models import (
    Analysis,
    AnalysisDraft,
    AnalysisQuality,
    AnalysisStatus,
    ClaimAudit,
    ClaimStatus,
    Document,
    Evidence,
    PublicationMeta,
    ResearchStatus,
    VerificationResult,
)
from daily_intel.intelligence.claims import (
    apply_claim_checks,
    blocking_claim_issues,
    check_numeric_claims,
    covering_claim_checks,
    extract_claims,
    field_is_supported,
    supported_text,
)
from daily_intel.intelligence.snapshots import (
    PLACEHOLDER_HEADLINE,
    documents_snapshot_hash,
    quote_span,
)


@dataclass(frozen=True, slots=True)
class QualityPolicy:
    version: str = "evidence-gate-v4"
    min_key_facts: int = 3
    max_key_facts: int = 6
    min_supported_evidence: int = 2
    max_supported_evidence: int = 6
    min_primary_sources: int = 1
    min_risks: int = 2
    min_counterpoints: int = 1
    max_industry_impacts: int = 4
    max_risks: int = 5
    max_counterpoints: int = 4
    downgrade_on_unsupported_claims: bool = True
    max_single_source_confidence: float = .85
    max_deep_confidence: float = .90
    lead_confidence_cap: float = .49
    section_max_chars: int = 900
    list_item_max_chars: int = 420
    plain_takeaway_max_chars: int = 280
    schema_version: str = "claim-audit-v2"

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> "QualityPolicy":
        config = settings.get("quality", {})
        defaults = cls()
        return cls(
            version=str(config.get("policy_version", defaults.version)),
            min_key_facts=int(config.get("min_key_facts", defaults.min_key_facts)),
            max_key_facts=int(config.get("max_key_facts", defaults.max_key_facts)),
            min_supported_evidence=int(config.get("min_supported_evidence", defaults.min_supported_evidence)),
            max_supported_evidence=int(config.get("max_supported_evidence", defaults.max_supported_evidence)),
            min_primary_sources=int(config.get("min_primary_sources", defaults.min_primary_sources)),
            min_risks=int(config.get("min_risks", defaults.min_risks)),
            min_counterpoints=int(config.get("min_counterpoints", defaults.min_counterpoints)),
            max_industry_impacts=int(config.get("max_industry_impacts", defaults.max_industry_impacts)),
            max_risks=int(config.get("max_risks", defaults.max_risks)),
            max_counterpoints=int(config.get("max_counterpoints", defaults.max_counterpoints)),
            downgrade_on_unsupported_claims=bool(
                config.get("downgrade_on_unsupported_claims", defaults.downgrade_on_unsupported_claims)
            ),
            max_single_source_confidence=float(
                config.get("max_single_source_confidence", defaults.max_single_source_confidence)
            ),
            max_deep_confidence=float(config.get("max_deep_confidence", defaults.max_deep_confidence)),
            lead_confidence_cap=float(config.get("lead_confidence_cap", defaults.lead_confidence_cap)),
            section_max_chars=int(config.get("section_max_chars", defaults.section_max_chars)),
            list_item_max_chars=int(config.get("list_item_max_chars", defaults.list_item_max_chars)),
            plain_takeaway_max_chars=int(
                config.get("plain_takeaway_max_chars", defaults.plain_takeaway_max_chars)
            ),
            schema_version=str(config.get("schema_version", defaults.schema_version)),
        )

    def prompt_contract(self) -> dict[str, Any]:
        return {
            "key_facts": {"min": self.min_key_facts, "max": self.max_key_facts},
            "evidence": {
                "min": self.min_supported_evidence,
                "max": self.max_supported_evidence,
                "quotes_must_be_exact": True,
            },
            "risks": {"min": self.min_risks, "max": self.max_risks},
            "counterpoints": {"min": self.min_counterpoints, "max": self.max_counterpoints},
            "industry_impacts_max": self.max_industry_impacts,
            "section_max_chars": self.section_max_chars,
            "plain_takeaway_max_chars": self.plain_takeaway_max_chars,
            "unsupported_claims_force_downgrade": self.downgrade_on_unsupported_claims,
            "claim_audit": {
                "schema_version": self.schema_version,
                "cover_every_assertion": True,
                "compare_tables_independently": True,
            },
            "readability": {
                "plain_takeaway_required": True,
                "explain_terms_on_first_use": True,
                "independent_scan_sentence": True,
            },
        }


@dataclass(slots=True)
class QualityDecision:
    draft: AnalysisDraft
    evidence: list[Evidence]
    quality: AnalysisQuality
    confidence: float
    claims: list[ClaimAudit]
    safe_draft: AnalysisDraft
    snapshot_hash: str = ""

    @property
    def deep(self) -> bool:
        return self.quality.passed


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value)


def exact_evidence_quote(quote: str, document: Document) -> str | None:
    """Return an exact source span, tolerating only model-normalized whitespace."""
    target = quote.strip()
    if not target:
        return None
    for source in (document.content, document.summary):
        if not source:
            continue
        if target in source:
            return target
        compact_target = _normalized(target)
        if not compact_target:
            continue
        compact_chars: list[str] = []
        source_indexes: list[int] = []
        for index, char in enumerate(source):
            if char.isspace():
                continue
            compact_chars.append(char)
            source_indexes.append(index)
        compact_source = "".join(compact_chars)
        start = compact_source.find(compact_target)
        if start < 0:
            continue
        end = start + len(compact_target) - 1
        exact = source[source_indexes[start]:source_indexes[end] + 1]
        if 8 <= len(exact) <= 1200:
            return exact
    return None


def _compact(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    if len(text) <= limit:
        return text
    head = text[:limit]
    boundary = max(head.rfind("。"), head.rfind("；"), head.rfind("."), head.rfind(";"))
    if boundary >= int(limit * .6):
        return head[: boundary + 1]
    return head.rstrip("，,：:；; ") + "…"


def _normalize_list(values: list[str], limit: int, item_limit: int) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _compact(value, item_limit)
        key = _normalized(item).casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append(item)
        if len(normalized) >= limit:
            break
    return normalized


def _whitespace_only_change(original: str, updated: str) -> bool:
    return _normalized(original) == _normalized(updated)


class AnalysisQualityGate:
    """Model-independent normalization and release gate for every analysis."""

    REQUIRED_SECTIONS = ("technical_mechanism", "novelty", "maturity", "outlook_6_24m")

    def __init__(self, policy: QualityPolicy) -> None:
        self.policy = policy

    def evaluate(
        self, draft: AnalysisDraft, verification: VerificationResult, documents: list[Document],
        *, claim_inventory: list[ClaimAudit] | None = None,
    ) -> QualityDecision:
        length_rewritten = self._length_rewrite_detected(draft)
        normalized_draft = self.normalize_draft(draft)
        claims = claim_inventory if claim_inventory is not None else extract_claims(normalized_draft if not length_rewritten else draft)
        if length_rewritten:
            claims = [
                item.model_copy(update={
                    "status": ClaimStatus.UNAUDITED,
                    "notes": "截断或编辑改变了已审查文本，必须重新审核",
                })
                for item in claims
            ]
        else:
            claims = apply_claim_checks(claims, verification)
            claims = self.ground_claims(claims, documents)
            claims = check_numeric_claims(claims, documents, verification.comparison_rows)
        evidence = self._verified_evidence(normalized_draft, verification, documents, claims=claims)
        documents_by_id = {item.id: item for item in documents}
        evidence_docs = [documents_by_id[item.document_id] for item in evidence]
        source_diversity = len({item.source_id for item in evidence_docs})
        primary_sources = len({item.source_id for item in evidence_docs if item.source_tier == 1})
        unsupported = _normalize_list(
            [
                *verification.unsupported_claims,
                *[item.text for item in claims if item.status in {ClaimStatus.UNSUPPORTED, ClaimStatus.CONFLICTING, ClaimStatus.UNVERIFIABLE}],
            ],
            12, self.policy.list_item_max_chars,
        )
        issues: list[str] = []
        expected_ids = {c.claim_id for c in extract_claims(draft)}
        received_ids = [c.claim_id for c in verification.claim_checks]
        if len(set(received_ids)) != len(received_ids) or set(received_ids) - expected_ids:
            issues.append("claim_protocol_error")
        if verification.verdict != "pass":
            issues.append("verifier_not_pass")
        issues.extend(blocking_claim_issues(claims))
        if unsupported and self.policy.downgrade_on_unsupported_claims and "unsupported_claims" not in issues:
            if any(item.status == ClaimStatus.UNSUPPORTED for item in claims) or verification.unsupported_claims:
                issues.append("unsupported_claims")
        if len(evidence) < self.policy.min_supported_evidence:
            issues.append("insufficient_evidence")
        if primary_sources < self.policy.min_primary_sources:
            issues.append("missing_primary_source")
        if not field_is_supported(claims, "headline") and "unaudited_claims" not in issues:
            # Headline is a key conclusion; uncovered/failing headline cannot be deep.
            if any(item.field_path == "headline" and item.status != ClaimStatus.SUPPORTED for item in claims):
                pass
        safe_draft = self._safe_draft(normalized_draft, claims)
        if len(safe_draft.key_facts) < self.policy.min_key_facts:
            issues.append("insufficient_key_facts")
        if not safe_draft.plain_takeaway.strip():
            issues.append("missing_plain_takeaway")
        if any(not getattr(safe_draft, name).strip() for name in self.REQUIRED_SECTIONS):
            issues.append("missing_required_sections")
        if len(safe_draft.risks) < self.policy.min_risks:
            issues.append("insufficient_risks")
        if len(safe_draft.counterpoints) < self.policy.min_counterpoints:
            issues.append("insufficient_counterpoints")
        if length_rewritten:
            issues.append("rewritten_after_audit")

        unique_issues = list(dict.fromkeys(issues))
        score = self._score(
            verification, unsupported, len(evidence), primary_sources,
            len(safe_draft.key_facts), safe_draft, unique_issues,
        )
        passed = not unique_issues
        confidence = max(0.0, min(1.0, normalized_draft.confidence + verification.confidence_adjustment))
        if source_diversity <= 1:
            confidence = min(confidence, self.policy.max_single_source_confidence)
        confidence = min(
            confidence,
            self.policy.max_deep_confidence if passed else self.policy.lead_confidence_cap,
        )
        quality = AnalysisQuality(
            policy_version=self.policy.version,
            passed=passed,
            score=score,
            supported_evidence=len(evidence),
            primary_sources=primary_sources,
            source_diversity=source_diversity,
            unsupported_claims=unsupported,
            issues=unique_issues,
            schema_version=self.policy.schema_version,
            claim_audits=claims,
        )
        return QualityDecision(
            normalized_draft, evidence, quality, confidence, claims, safe_draft,
            snapshot_hash=documents_snapshot_hash(documents),
        )

    def build_analysis(
        self, event_id: str, decision: QualityDecision,
        model: str, prompt_version: str, created_at: datetime, lane: str = "hardcore",
        *, topic_id: str = "", topic_name: str = "",
        source_headline: str = "",
    ) -> Analysis:
        reading = "general" if lane == "general" else "hardcore"
        draft = decision.draft
        safe = decision.safe_draft
        disputed = [
            item.text for item in decision.claims
            if item.status in {
                ClaimStatus.UNSUPPORTED, ClaimStatus.CONFLICTING, ClaimStatus.UNVERIFIABLE,
            }
        ]
        publication = PublicationMeta(
            source_headline=source_headline or draft.headline,
            disputed_claims=disputed[:8],
            snapshot_hash=decision.snapshot_hash,
            schema_version=self.policy.schema_version,
            unverified_placeholder=not field_is_supported(decision.claims, "headline") and not decision.deep,
        )
        if decision.deep:
            publication.research_status = ResearchStatus.DEEP_PASS.value
            return Analysis(
                event_id=event_id, status=AnalysisStatus.DEEP,
                headline=safe.headline, scan_sentence=safe.scan_sentence,
                plain_takeaway=safe.plain_takeaway, key_facts=safe.key_facts,
                technical_mechanism=safe.technical_mechanism,
                novelty=safe.novelty, maturity=safe.maturity,
                outlook_6_24m=safe.outlook_6_24m,
                industry_impacts=safe.industry_impacts,
                risks=safe.risks, counterpoints=safe.counterpoints,
                confidence=decision.confidence, evidence=decision.evidence,
                quality=decision.quality,
                model=model, prompt_version=prompt_version, created_at=created_at,
                lane=reading, topic_id=topic_id, topic_name=topic_name,
                publication=publication,
            )
        publication.research_status = ResearchStatus.QUALITY_DOWNGRADE.value
        excerpts = [
            (document.content or document.summary or document.title).strip()[:300]
            for document in []
        ]
        return Analysis(
            event_id=event_id, status=AnalysisStatus.LEAD,
            headline=safe.headline,
            scan_sentence=safe.scan_sentence,
            plain_takeaway=safe.plain_takeaway,
            key_facts=safe.key_facts,
            risks=["部分结论尚未完成核验，请结合下方审核原因阅读。"],
            confidence=decision.confidence, evidence=decision.evidence,
            quality=decision.quality, model=model,
            prompt_version=prompt_version, created_at=created_at,
            lane=reading, topic_id=topic_id, topic_name=topic_name,
            publication=publication.model_copy(update={
                "source_excerpts": excerpts,
            }),
        )

    def _length_rewrite_detected(self, draft: AnalysisDraft) -> bool:
        item_limit = self.policy.list_item_max_chars
        if not _whitespace_only_change(draft.headline, _compact(draft.headline, 220)):
            return True
        if not _whitespace_only_change(
            draft.plain_takeaway, _compact(draft.plain_takeaway, self.policy.plain_takeaway_max_chars)
        ):
            return True
        if draft.scan_sentence and not _whitespace_only_change(
            draft.scan_sentence, _compact(draft.scan_sentence, 220)
        ):
            return True
        for value in draft.key_facts:
            if not _whitespace_only_change(value, _compact(value, item_limit)):
                return True
        return False

    def _safe_draft(self, draft: AnalysisDraft, claims: list[ClaimAudit]) -> AnalysisDraft:
        headline = draft.headline
        if not field_is_supported(claims, "headline"):
            headline = PLACEHOLDER_HEADLINE
        scan = draft.scan_sentence if field_is_supported(claims, "scan_sentence") else ""
        takeaway_parts = supported_text(claims, "plain_takeaway")
        facts = supported_text(claims, "key_facts")
        mechanism = draft.technical_mechanism if field_is_supported(claims, "technical_mechanism") else ""
        novelty = draft.novelty if field_is_supported(claims, "novelty") else ""
        maturity = draft.maturity if field_is_supported(claims, "maturity") else ""
        outlook = draft.outlook_6_24m if field_is_supported(claims, "outlook_6_24m") else ""
        impacts = []
        for index, impact in enumerate(draft.industry_impacts):
            if field_is_supported(claims, f"industry_impacts[{index}]"):
                impacts.append(impact)
        return draft.model_copy(update={
            "headline": headline,
            "scan_sentence": scan,
            "plain_takeaway": "".join(takeaway_parts) if takeaway_parts else (
                draft.plain_takeaway if field_is_supported(claims, "plain_takeaway") else ""
            ),
            "key_facts": facts[: self.policy.max_key_facts],
            "technical_mechanism": mechanism,
            "novelty": novelty,
            "maturity": maturity,
            "outlook_6_24m": outlook,
            "industry_impacts": impacts[: self.policy.max_industry_impacts],
        })

    def normalize_draft(self, draft: AnalysisDraft) -> AnalysisDraft:
        item_limit = self.policy.list_item_max_chars
        impacts = []
        seen_impacts: set[tuple[str, str, str, str]] = set()
        for impact in draft.industry_impacts:
            normalized = impact.model_copy(update={
                "segment": _compact(impact.segment, 120),
                "rationale": _compact(impact.rationale, item_limit),
            })
            key = (
                _normalized(normalized.segment).casefold(),
                normalized.direction,
                normalized.horizon,
                _normalized(normalized.rationale).casefold(),
            )
            if not key[0] or key in seen_impacts:
                continue
            seen_impacts.add(key)
            impacts.append(normalized)
            if len(impacts) >= self.policy.max_industry_impacts:
                break
        return draft.model_copy(update={
            "headline": _compact(draft.headline, 220),
            "scan_sentence": _compact(draft.scan_sentence, 220),
            "plain_takeaway": _compact(draft.plain_takeaway, self.policy.plain_takeaway_max_chars),
            "key_facts": _normalize_list(
                draft.key_facts, self.policy.max_key_facts, item_limit
            ),
            "technical_mechanism": _compact(draft.technical_mechanism, self.policy.section_max_chars),
            "novelty": _compact(draft.novelty, self.policy.section_max_chars),
            "maturity": _compact(draft.maturity, self.policy.section_max_chars),
            "outlook_6_24m": _compact(draft.outlook_6_24m, self.policy.section_max_chars),
            "industry_impacts": impacts,
            "risks": _normalize_list(draft.risks, self.policy.max_risks, item_limit),
            "counterpoints": _normalize_list(
                draft.counterpoints, self.policy.max_counterpoints, item_limit
            ),
        })

    def ground_claims(self, claims: list[ClaimAudit], documents: list[Document]) -> list[ClaimAudit]:
        by_id = {d.id: d for d in documents}
        grounded = []
        for claim in claims:
            if claim.status != ClaimStatus.SUPPORTED:
                grounded.append(claim)
                continue
            doc = by_id.get(claim.document_id)
            quote = exact_evidence_quote(claim.quote, doc) if doc else None
            if quote is None:
                grounded.append(claim.model_copy(update={
                    "status": ClaimStatus.UNVERIFIABLE,
                    "notes": "审核引用缺失或不是所供文档的连续原文",
                }))
            else:
                grounded.append(claim.model_copy(update={"quote": quote, "snapshot_hash": documents_snapshot_hash(documents)}))
        return grounded

    def _verified_evidence(
        self, draft: AnalysisDraft, verification: VerificationResult, documents: list[Document],
        *, claims: list[ClaimAudit],
    ) -> list[Evidence]:
        documents_by_id = {item.id: item for item in documents}
        accepted: list[Evidence] = []
        seen: set[tuple[str, str]] = set()
        snapshot = documents_snapshot_hash(documents)
        supported = [c for c in claims if c.status == ClaimStatus.SUPPORTED]
        linked_ids = {c.document_id for c in supported}
        candidates = [draft.evidence[i] for i in verification.supported_evidence_indexes if 0 <= i < len(draft.evidence)]
        if not verification.supported_evidence_indexes:
            # A supported claim's exact quotation is itself an explicit verifier judgement.
            candidates = [Evidence(document_id=c.document_id, url=documents_by_id[c.document_id].url,
                                   quote=c.quote, locator=c.locator or "审核原文")
                          for c in supported if c.document_id in documents_by_id and 8 <= len(c.quote) <= 1200]
        primary_id = next((c.document_id for c in supported if c.field_path == "headline"), "")
        candidates.sort(key=lambda e: e.document_id != primary_id)
        for evidence in candidates:
            if evidence.document_id not in linked_ids:
                continue
            document = documents_by_id.get(evidence.document_id)
            if document is None or evidence.url.rstrip("/") != document.url.rstrip("/"):
                continue
            exact_quote = exact_evidence_quote(evidence.quote, document)
            if exact_quote is None:
                continue
            key = (evidence.document_id, _normalized(exact_quote))
            if key in seen:
                continue
            seen.add(key)
            source = document.content or document.summary or ""
            span = quote_span(exact_quote, source)
            accepted.append(evidence.model_copy(update={
                "quote": exact_quote,
                "snapshot_hash": snapshot,
                "char_start": None if span is None else span[0],
                "char_end": None if span is None else span[1],
            }))
            if len(accepted) >= self.policy.max_supported_evidence:
                break
        return accepted

    def _score(
        self, verification: VerificationResult, unsupported: list[str], evidence_count: int,
        primary_sources: int, fact_count: int, draft: AnalysisDraft, issues: list[str],
    ) -> int:
        score = 0.0
        score += 20 if verification.verdict == "pass" else 0
        claim_blockers = {
            "unsupported_claims", "conflicting_claims", "unverifiable_claims", "unaudited_claims",
        }
        score += 15 if not unsupported and not claim_blockers.intersection(issues) else 0
        score += 25 * min(1.0, evidence_count / max(1, self.policy.min_supported_evidence))
        score += 15 if primary_sources >= self.policy.min_primary_sources else 0
        score += 10 * min(1.0, fact_count / max(1, self.policy.min_key_facts))
        sections_complete = all(
            getattr(draft, name).strip() for name in self.REQUIRED_SECTIONS
        ) and bool(draft.plain_takeaway.strip())
        score += 10 if sections_complete else 0
        score += 5 if not {"insufficient_risks", "insufficient_counterpoints"}.intersection(issues) else 0
        return int(round(max(0.0, min(100.0, score))))


def summarize_quality(
    analyses: list[Analysis], *,
    research_target_ids: set[str] | None = None,
    cache_hits: int = 0,
    cache_misses: int = 0,
) -> dict[str, Any]:
    if not analyses:
        return {
            "policy_version": "",
            "average_score": 0,
            "research_average_score": 0,
            "passed": 0,
            "downgraded": 0,
            "issue_counts": {},
            "research_targets": 0,
            "new_calls": cache_misses,
            "cache_hits": cache_hits,
            "deep_passed": 0,
            "quality_downgraded": 0,
            "research_failed": 0,
            "broad_only": 0,
            "material_gate_extensive": 0,
            "intensive_published": 0,
            "extensive_published": 0,
        }
    issues = Counter(issue for item in analyses for issue in item.quality.issues)
    versions = sorted({item.quality.policy_version for item in analyses})
    research_ids = research_target_ids or {
        item.event_id for item in analyses if item.publication.was_research_target
    }
    research = [item for item in analyses if item.event_id in research_ids]
    def _status(item: Analysis) -> str:
        return item.publication.research_status or (
            ResearchStatus.DEEP_PASS.value if item.status == AnalysisStatus.DEEP
            else ResearchStatus.BROAD_ONLY.value if "broad_reading_only" in item.quality.issues
            else ResearchStatus.QUALITY_DOWNGRADE.value
        )
    statuses = Counter(_status(item) for item in analyses)
    return {
        "policy_version": ",".join(versions),
        "average_score": round(sum(item.quality.score for item in analyses) / len(analyses), 1),
        "research_average_score": (
            round(sum(item.quality.score for item in research) / len(research), 1) if research else 0
        ),
        "passed": sum(item.quality.passed for item in analyses),
        "downgraded": sum(not item.quality.passed for item in research),
        "issue_counts": dict(sorted(issues.items())),
        "research_targets": len(research_ids),
        "new_calls": cache_misses,
        "cache_hits": cache_hits,
        "deep_passed": statuses[ResearchStatus.DEEP_PASS.value],
        "quality_downgraded": statuses[ResearchStatus.QUALITY_DOWNGRADE.value] + statuses[ResearchStatus.MATERIAL_GATE_EXTENSIVE.value],
        "research_failed": statuses[ResearchStatus.RESEARCH_FAILED.value],
        "broad_only": statuses[ResearchStatus.BROAD_ONLY.value],
        "material_gate_extensive": sum(item.publication.material_gate_passed is False and item.publication.was_research_target and _status(item) != ResearchStatus.RESEARCH_FAILED.value for item in analyses),
        "intensive_published": sum(
            item.publication.publication_tier == "intensive" for item in analyses
        ),
        "extensive_published": sum(
            item.publication.publication_tier == "extensive" for item in analyses
        ),
    }


# Re-export for tests that patch or import covering checks via quality.
__all__ = [
    "AnalysisQualityGate",
    "QualityDecision",
    "QualityPolicy",
    "covering_claim_checks",
    "exact_evidence_quote",
    "summarize_quality",
]
