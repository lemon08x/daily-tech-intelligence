from __future__ import annotations

from datetime import datetime, timezone

from daily_intel.core.models import (
    Analysis,
    AnalysisDraft,
    AnalysisStatus,
    ClaimCheck,
    Document,
    Evidence,
    IndustryImpact,
    VerificationResult,
)
from daily_intel.intelligence.snapshots import PLACEHOLDER_HEADLINE, stage_documents
from daily_intel.intelligence.claims import covering_claim_checks
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


def _verification(draft: AnalysisDraft | None = None, **updates) -> VerificationResult:
    payload = {
        "supported_evidence_indexes": list(range(12)),
        "unsupported_claims": [],
        "claim_checks": covering_claim_checks(draft) if draft is not None else [],
        "confidence_adjustment": 0,
        "verdict": "pass",
        "notes": "checked",
    }
    payload.update(updates)
    return VerificationResult(**payload)


def test_quality_gate_normalizes_model_verbosity_to_one_contract() -> None:
    gate = AnalysisQualityGate(QualityPolicy())
    draft = _draft()
    decision = gate.evaluate(draft, _verification(draft), [_document()])
    assert decision.deep
    assert len(decision.draft.key_facts) == 6
    assert len(decision.draft.industry_impacts) == 4
    assert len(decision.draft.risks) == 5
    assert len(decision.draft.counterpoints) == 4
    assert len(decision.evidence) == 6


def test_pass_verdict_with_unsupported_claims_is_still_downgraded() -> None:
    gate = AnalysisQualityGate(QualityPolicy())
    draft = _draft()
    decision = gate.evaluate(
        draft,
        _verification(
            draft,
            unsupported_claims=["The production claim is not present in the source."],
            claim_checks=covering_claim_checks(draft),
        ),
        [_document()],
    )
    assert not decision.deep
    assert "unsupported_claims" in decision.quality.issues
    analysis = gate.build_analysis("event-1", decision, "model-x", "prompt-v2", NOW)
    assert analysis.status == AnalysisStatus.LEAD
    assert analysis.technical_mechanism == ""


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
        _verification(_draft(evidence=evidence), supported_evidence_indexes=[0, 1, 2]),
        [_document()],
    )
    assert len(decision.evidence) == 1
    assert "insufficient_evidence" in decision.quality.issues


def test_quality_gate_publishes_exact_source_span_after_whitespace_normalization() -> None:
    document = _document().model_copy(update={
        "content": (
            "First supported result spans\nmultiple source lines with technical detail. "
            "Second supported result also spans\nmultiple source lines with benchmark detail."
        ),
        "summary": "",
    })
    evidence = [
        Evidence(
            document_id=document.id, url=document.url,
            quote="First supported result spans multiple source lines with technical detail.",
            locator="p1",
        ),
        Evidence(
            document_id=document.id, url=document.url,
            quote="Second supported result also spans multiple source lines with benchmark detail.",
            locator="p2",
        ),
    ]
    draft = _draft(evidence=evidence)
    decision = AnalysisQualityGate(QualityPolicy()).evaluate(
        draft,
        _verification(draft, supported_evidence_indexes=[0, 1]),
        [document],
    )
    assert decision.deep
    assert all(item.quote in document.content for item in decision.evidence)
    assert "spans\nmultiple" in decision.evidence[0].quote


def test_duplicate_facts_do_not_game_minimum_and_single_source_caps_confidence() -> None:
    gate = AnalysisQualityGate(QualityPolicy())
    duplicate_decision = gate.evaluate(
        _draft(key_facts=["same fact", " same  fact ", "same fact"]),
        _verification(),
        [_document()],
    )
    assert len(duplicate_decision.draft.key_facts) == 1
    assert "insufficient_key_facts" in duplicate_decision.quality.issues

    accepted_draft = _draft()
    accepted = gate.evaluate(accepted_draft, _verification(accepted_draft), [_document()])
    assert accepted.deep
    assert accepted.confidence == QualityPolicy().max_single_source_confidence


def _comparison_document(body: str) -> Document:
    return _document().model_copy(update={"content": body, "summary": body})


def test_r1_table_conflict_rejects_leading_f1_even_if_verifier_passes() -> None:
    body = """
The authors report that SENTINEL-RL leads in precision and F1.
Table III. Detection results on the shared benchmark.
| Method | Precision | Recall | F1 |
| --- | --- | --- | --- |
| LMDetect | 0.91 | 0.93 | 0.92 |
| SENTINEL-RL | 0.90 | 0.88 | 0.89 |
"""
    draft = _draft(
        key_facts=["SENTINEL-RL 的 F1 为 0.89，精度和 F1 领先"],
    ).model_copy(update={
        "headline": "SENTINEL-RL 在检测基准上 F1 领先",
        "plain_takeaway": "SENTINEL-RL 报告 F1 0.89，精度和 F1 领先。独立复现仍待进行。",
        "scan_sentence": "SENTINEL-RL 在检测任务中 F1 达到 0.89 并宣称领先。",
    })
    checks = covering_claim_checks(draft)
    decision = AnalysisQualityGate(QualityPolicy()).evaluate(
        draft, _verification(draft, claim_checks=checks), [_comparison_document(body)],
    )
    assert not decision.deep
    assert "conflicting_claims" in decision.quality.issues
    analysis = AnalysisQualityGate(QualityPolicy()).build_analysis(
        "sentinel", decision, "model-x", "v", NOW,
    )
    assert "领先" not in analysis.headline
    assert analysis.headline == PLACEHOLDER_HEADLINE or "领先" not in "".join(analysis.key_facts)


def test_r2_evidence_count_cannot_replace_claim_coverage() -> None:
    draft = _draft()
    decision = AnalysisQualityGate(QualityPolicy()).evaluate(
        draft, _verification(), [_document()],
    )
    assert not decision.deep
    assert "unaudited_claims" in decision.quality.issues


def test_r3_percentage_points_are_not_relative_percent() -> None:
    draft = _draft().model_copy(update={
        "headline": "准确率从 54.0% 到 64.2%，提升 10.2%",
        "plain_takeaway": "准确率从 54.0% 到 64.2%，提升 10.2%。后续仍需复核。",
        "key_facts": ["准确率从 54.0% 到 64.2%，提升 10.2%"],
    })
    decision = AnalysisQualityGate(QualityPolicy()).evaluate(
        draft, _verification(draft), [_document()],
    )
    assert not decision.deep
    assert "conflicting_claims" in decision.quality.issues

    lower = _draft().model_copy(update={
        "headline": "误差从 54.0% 到 64.2%，提升 10.2 个百分点",
        "plain_takeaway": "误差从 54.0% 到 64.2%，提升 10.2 个百分点。越低越好。",
        "key_facts": ["误差从 54.0% 到 64.2%，该指标越低越好"],
    })
    lower_decision = AnalysisQualityGate(QualityPolicy()).evaluate(
        lower, _verification(lower), [_document()],
    )
    assert not lower_decision.deep


def test_r4_unscoped_highest_gain_is_stripped_from_publication() -> None:
    body = """
GIFT reports a 12.6 percentage point gain on one pick-and-place split.
RoboCasa articulated tasks show a 24.6 percentage point improvement under the same protocol.
"""
    draft = _draft().model_copy(update={
        "headline": "最高提升 12.6 个百分点",
        "scan_sentence": "该方法最高提升 12.6 个百分点。",
        "plain_takeaway": "作者称最高提升 12.6 个百分点。独立验证仍不足。",
        "key_facts": ["最高提升 12.6 个百分点", "RoboCasa 铰接任务另有 24.6 个百分点提升", "仍需独立复现"],
    })
    decision = AnalysisQualityGate(QualityPolicy()).evaluate(
        draft, _verification(draft), [_comparison_document(body)],
    )
    analysis = AnalysisQualityGate(QualityPolicy()).build_analysis(
        "gift", decision, "model-x", "v", NOW, source_headline=draft.headline,
    )
    assert analysis.status == AnalysisStatus.LEAD
    assert analysis.headline != "最高提升 12.6 个百分点"
    assert analysis.headline == PLACEHOLDER_HEADLINE
    assert "最高提升 12.6" not in analysis.scan_sentence
    scoped = _draft().model_copy(update={
        "headline": "在 pick-and-place 子集上提升 12.6 个百分点",
        "plain_takeaway": "在 pick-and-place 子集上提升 12.6 个百分点。其他任务另计。",
        "key_facts": ["在 pick-and-place 子集上提升 12.6 个百分点", "材料说明了方法", "仍需复核"],
        "scan_sentence": "在 pick-and-place 子集上提升 12.6 个百分点。",
    })
    scoped_decision = AnalysisQualityGate(QualityPolicy()).evaluate(
        scoped, _verification(scoped), [_comparison_document(body)],
    )
    assert "最高" not in scoped.headline


def test_r5_missing_headers_block_overall_lead() -> None:
    body = """
Results were reported without recoverable headers.
0.89 0.92
"""
    draft = _draft().model_copy(update={
        "headline": "新方法全面领先",
        "plain_takeaway": "新方法全面领先。细节仍待核对。",
        "key_facts": ["新方法全面领先", "材料给出了两个数字", "条件未写清"],
        "scan_sentence": "新方法全面领先。",
    })
    decision = AnalysisQualityGate(QualityPolicy()).evaluate(
        draft, _verification(draft), [_comparison_document(body)],
    )
    assert not decision.deep
    assert "unverifiable_claims" in decision.quality.issues or "conflicting_claims" in decision.quality.issues


def test_r7_stripping_unsupported_facts_rechecks_minimums() -> None:
    draft = _draft(key_facts=["supported fact one", "bad leading claim 领先", "supported fact two"])
    checks = [
        ClaimCheck(claim_id="headline", field_path="headline", text=draft.headline, status="supported"),
        ClaimCheck(claim_id="plain_takeaway.0", field_path="plain_takeaway[0]", text=draft.plain_takeaway, status="supported"),
        ClaimCheck(claim_id="key_facts.0", field_path="key_facts[0]", text="supported fact one", status="supported"),
        ClaimCheck(claim_id="key_facts.1", field_path="key_facts[1]", text="bad leading claim 领先", status="unsupported"),
        ClaimCheck(claim_id="key_facts.2", field_path="key_facts[2]", text="supported fact two", status="supported"),
        ClaimCheck(claim_id="technical_mechanism", field_path="technical_mechanism", text=draft.technical_mechanism, status="supported"),
        ClaimCheck(claim_id="novelty", field_path="novelty", text=draft.novelty, status="supported"),
        ClaimCheck(claim_id="maturity", field_path="maturity", text=draft.maturity, status="supported"),
        ClaimCheck(claim_id="outlook_6_24m", field_path="outlook_6_24m", text=draft.outlook_6_24m, status="supported"),
    ]
    for index, impact in enumerate(draft.industry_impacts):
        checks.append(ClaimCheck(
            claim_id=f"industry_impacts.{index}",
            field_path=f"industry_impacts[{index}]",
            text=f"{impact.segment} {impact.horizon} {impact.direction}: {impact.rationale}",
            status="supported",
        ))
    decision = AnalysisQualityGate(QualityPolicy()).evaluate(
        draft, _verification(draft, claim_checks=checks), [_document()],
    )
    assert not decision.deep
    analysis = AnalysisQualityGate(QualityPolicy()).build_analysis(
        "thin", decision, "model-x", "v", NOW,
    )
    assert "bad leading claim" not in " ".join(analysis.key_facts)
    assert "insufficient_key_facts" in decision.quality.issues


def test_r12_clipped_stage_documents_cannot_use_unseen_tail() -> None:
    long_body = ("visible prefix. " * 20) + "HIDDEN_TAIL_EVIDENCE_ONLY_AFTER_CLIP " + ("tail " * 40)
    document = _document().model_copy(update={"content": long_body, "summary": ""})
    clipped = stage_documents([document], 80)
    assert "HIDDEN_TAIL_EVIDENCE_ONLY_AFTER_CLIP" not in clipped[0].content
    draft = _draft(evidence=[
        Evidence(
            document_id="doc-1", url=document.url,
            quote="HIDDEN_TAIL_EVIDENCE_ONLY_AFTER_CLIP", locator="tail",
        ),
        Evidence(
            document_id="doc-1", url=document.url,
            quote="visible prefix.", locator="head",
        ),
    ])
    decision = AnalysisQualityGate(QualityPolicy()).evaluate(
        draft, _verification(draft, supported_evidence_indexes=[0, 1]), clipped,
    )
    assert all("HIDDEN_TAIL" not in item.quote for item in decision.evidence)
    assert "insufficient_evidence" in decision.quality.issues or not decision.deep


