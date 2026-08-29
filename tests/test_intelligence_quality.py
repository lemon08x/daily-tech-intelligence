from __future__ import annotations

from datetime import datetime, timezone

from daily_intel.core.models import (
    Analysis,
    AnalysisDraft,
    AnalysisStatus,
    Document,
    Evidence,
    IndustryImpact,
    VerificationResult,
)
from daily_intel.intelligence.quality import AnalysisQualityGate, QualityPolicy


NOW = datetime(2026, 8, 25, 10, tzinfo=timezone.utc)
QUOTES = [f"Exact source quotation number {index} with technical detail." for index in range(12)]


def _document(source_id: str = "primary") -> Document:
    content = " ".join(QUOTES)
    return Document(
        id="doc-1",
        source_id=source_id,
        source_name="Primary",
        external_id="1",
        title="Technical release",
        url="https://example.com/technical-release",
        canonical_url="https://example.com/technical-release",
        published_at=NOW,
        fetched_at=NOW,
        summary=content,
        content=content,
        content_hash="a" * 64,
        source_tier=1,
    )


def _draft(
    *,
    key_facts: list[str] | None = None,
    evidence: list[Evidence] | None = None,
    confidence: float = .95,
) -> AnalysisDraft:
    return AnalysisDraft(
        headline="A normalized technical analysis",
        plain_takeaway="The source published a concrete engineering change that matters for deployment.",
        key_facts=key_facts or [f"Distinct supported fact {index}" for index in range(12)],
        technical_mechanism="A concrete mechanism described by the primary source.",
        novelty="A measurable engineering improvement.",
        maturity="Prototype stage with a published benchmark.",
        outlook_6_24m="Adoption depends on independent replication and production cost.",
        industry_impacts=[
            IndustryImpact(
                segment=f"segment-{index}",
                direction="uncertain",
                horizon="6-12m",
                rationale=f"Impact rationale {index}.",
            )
            for index in range(12)
        ],
        risks=[f"Distinct risk {index}" for index in range(12)],
        counterpoints=[f"Distinct counterpoint {index}" for index in range(12)],
        confidence=confidence,
        evidence=evidence or [
            Evidence(
                document_id="doc-1",
                url="https://example.com/technical-release",
                quote=quote,
                locator=f"paragraph-{index}",
            )
            for index, quote in enumerate(QUOTES)
        ],
    )


def _verification(**updates) -> VerificationResult:
    payload = {
        "supported_evidence_indexes": list(range(12)),
        "unsupported_claims": [],
        "confidence_adjustment": 0,
        "verdict": "pass",
        "notes": "checked",
    }
    payload.update(updates)
    return VerificationResult(**payload)


def test_quality_gate_normalizes_model_verbosity_to_one_contract() -> None:
    gate = AnalysisQualityGate(QualityPolicy())
    decision = gate.evaluate(_draft(), _verification(), [_document()])
    assert decision.deep
    assert len(decision.draft.key_facts) == 6
    assert len(decision.draft.industry_impacts) == 4
    assert len(decision.draft.risks) == 5
    assert len(decision.draft.counterpoints) == 4
    assert len(decision.evidence) == 6


def test_pass_verdict_with_unsupported_claims_is_still_downgraded() -> None:
    gate = AnalysisQualityGate(QualityPolicy())
    decision = gate.evaluate(
        _draft(),
        _verification(unsupported_claims=["The production claim is not present in the source."]),
        [_document()],
    )
    assert not decision.deep
    assert "unsupported_claims" in decision.quality.issues
    analysis = gate.build_analysis("event-1", decision, "model-x", "prompt-v2", NOW)
    assert analysis.status == AnalysisStatus.LEAD
    assert analysis.technical_mechanism == ""
    assert analysis.company_mappings == []


def test_duplicate_and_fabricated_quotes_cannot_satisfy_evidence_gate() -> None:
    evidence = [
        Evidence(
            document_id="doc-1",
            url="https://example.com/technical-release",
            quote=QUOTES[0],
            locator="p1",
        ),
        Evidence(
            document_id="doc-1",
            url="https://example.com/technical-release",
            quote=QUOTES[0],
            locator="duplicate",
        ),
        Evidence(
            document_id="doc-1",
            url="https://example.com/technical-release",
            quote="A fabricated quotation that is not in the source.",
            locator="fake",
        ),
    ]
    decision = AnalysisQualityGate(QualityPolicy()).evaluate(
        _draft(evidence=evidence),
        _verification(supported_evidence_indexes=[0, 1, 2]),
        [_document()],
    )
    assert len(decision.evidence) == 1
    assert "insufficient_evidence" in decision.quality.issues


def test_duplicate_facts_do_not_game_minimum_and_single_source_caps_confidence() -> None:
    gate = AnalysisQualityGate(QualityPolicy())
    duplicate_decision = gate.evaluate(
        _draft(key_facts=["same fact", " same  fact ", "same fact"]),
        _verification(),
        [_document()],
    )
    assert len(duplicate_decision.draft.key_facts) == 1
    assert "insufficient_key_facts" in duplicate_decision.quality.issues

    accepted = gate.evaluate(_draft(), _verification(), [_document()])
    assert accepted.deep
    assert accepted.confidence == QualityPolicy().max_single_source_confidence


