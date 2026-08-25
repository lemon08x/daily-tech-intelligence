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
    CompanyMapping,
    Document,
    Evidence,
    VerificationResult,
)


@dataclass(frozen=True, slots=True)
class QualityPolicy:
    version: str = "evidence-gate-v1"
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
            "unsupported_claims_force_downgrade": self.downgrade_on_unsupported_claims,
        }


@dataclass(slots=True)
class QualityDecision:
    draft: AnalysisDraft
    evidence: list[Evidence]
    quality: AnalysisQuality
    confidence: float

    @property
    def deep(self) -> bool:
        return self.quality.passed


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value)


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


class AnalysisQualityGate:
    """Model-independent normalization and release gate for every analysis."""

    REQUIRED_SECTIONS = ("technical_mechanism", "novelty", "maturity", "outlook_6_24m")

    def __init__(self, policy: QualityPolicy) -> None:
        self.policy = policy

    def evaluate(
        self, draft: AnalysisDraft, verification: VerificationResult, documents: list[Document],
    ) -> QualityDecision:
        normalized_draft = self._normalize_draft(draft)
        evidence = self._verified_evidence(normalized_draft, verification, documents)
        documents_by_id = {item.id: item for item in documents}
        evidence_docs = [documents_by_id[item.document_id] for item in evidence]
        source_diversity = len({item.source_id for item in evidence_docs})
        primary_sources = len({item.source_id for item in evidence_docs if item.source_tier == 1})
        unsupported = _normalize_list(
            verification.unsupported_claims, 12, self.policy.list_item_max_chars
        )
        issues: list[str] = []
        if verification.verdict != "pass":
            issues.append("verifier_not_pass")
        if unsupported and self.policy.downgrade_on_unsupported_claims:
            issues.append("unsupported_claims")
        if len(evidence) < self.policy.min_supported_evidence:
            issues.append("insufficient_evidence")
        if primary_sources < self.policy.min_primary_sources:
            issues.append("missing_primary_source")
        if len(normalized_draft.key_facts) < self.policy.min_key_facts:
            issues.append("insufficient_key_facts")
        if any(not getattr(normalized_draft, name).strip() for name in self.REQUIRED_SECTIONS):
            issues.append("missing_required_sections")
        if len(normalized_draft.risks) < self.policy.min_risks:
            issues.append("insufficient_risks")
        if len(normalized_draft.counterpoints) < self.policy.min_counterpoints:
            issues.append("insufficient_counterpoints")

        score = self._score(
            verification, unsupported, len(evidence), primary_sources,
            len(normalized_draft.key_facts), normalized_draft, issues,
        )
        passed = not issues
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
            issues=issues,
        )
        return QualityDecision(normalized_draft, evidence, quality, confidence)

    def build_analysis(
        self, event_id: str, decision: QualityDecision, mappings: list[CompanyMapping],
        model: str, prompt_version: str, created_at: datetime,
    ) -> Analysis:
        draft = decision.draft
        if decision.deep:
            return Analysis(
                event_id=event_id, status=AnalysisStatus.DEEP,
                headline=draft.headline, key_facts=draft.key_facts,
                technical_mechanism=draft.technical_mechanism,
                novelty=draft.novelty, maturity=draft.maturity,
                outlook_6_24m=draft.outlook_6_24m,
                industry_impacts=draft.industry_impacts,
                risks=draft.risks, counterpoints=draft.counterpoints,
                confidence=decision.confidence, evidence=decision.evidence,
                company_mappings=mappings, quality=decision.quality,
                model=model, prompt_version=prompt_version, created_at=created_at,
            )
        return Analysis(
            event_id=event_id, status=AnalysisStatus.LEAD,
            headline=draft.headline, key_facts=draft.key_facts,
            risks=["质量门降级：" + "、".join(decision.quality.issues)],
            confidence=decision.confidence, evidence=decision.evidence,
            quality=decision.quality, model=model,
            prompt_version=prompt_version, created_at=created_at,
        )

    def _normalize_draft(self, draft: AnalysisDraft) -> AnalysisDraft:
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
            "company_hypotheses": draft.company_hypotheses[:3],
        })

    def _verified_evidence(
        self, draft: AnalysisDraft, verification: VerificationResult, documents: list[Document],
    ) -> list[Evidence]:
        documents_by_id = {item.id: item for item in documents}
        accepted: list[Evidence] = []
        seen: set[tuple[str, str]] = set()
        for index in verification.supported_evidence_indexes:
            if index < 0 or index >= len(draft.evidence):
                continue
            evidence = draft.evidence[index]
            document = documents_by_id.get(evidence.document_id)
            if document is None or evidence.url.rstrip("/") != document.url.rstrip("/"):
                continue
            quote = _normalized(evidence.quote)
            source_text = _normalized(f"{document.content}\n{document.summary}")
            if not quote or quote not in source_text:
                continue
            key = (evidence.document_id, quote)
            if key in seen:
                continue
            seen.add(key)
            accepted.append(evidence)
            if len(accepted) >= self.policy.max_supported_evidence:
                break
        return accepted

    def _score(
        self, verification: VerificationResult, unsupported: list[str], evidence_count: int,
        primary_sources: int, fact_count: int, draft: AnalysisDraft, issues: list[str],
    ) -> int:
        score = 0.0
        score += 20 if verification.verdict == "pass" else 0
        score += 15 if not unsupported else 0
        score += 25 * min(1.0, evidence_count / max(1, self.policy.min_supported_evidence))
        score += 15 if primary_sources >= self.policy.min_primary_sources else 0
        score += 10 * min(1.0, fact_count / max(1, self.policy.min_key_facts))
        score += 10 if all(getattr(draft, name).strip() for name in self.REQUIRED_SECTIONS) else 0
        score += 5 if not {"insufficient_risks", "insufficient_counterpoints"}.intersection(issues) else 0
        return int(round(max(0.0, min(100.0, score))))


def summarize_quality(analyses: list[Analysis]) -> dict[str, Any]:
    if not analyses:
        return {
            "policy_version": "",
            "average_score": 0,
            "passed": 0,
            "downgraded": 0,
            "issue_counts": {},
        }
    issues = Counter(issue for item in analyses for issue in item.quality.issues)
    versions = sorted({item.quality.policy_version for item in analyses})
    return {
        "policy_version": ",".join(versions),
        "average_score": round(sum(item.quality.score for item in analyses) / len(analyses), 1),
        "passed": sum(item.quality.passed for item in analyses),
        "downgraded": sum(not item.quality.passed for item in analyses),
        "issue_counts": dict(sorted(issues.items())),
    }
