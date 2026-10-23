import pytest

from daily_intel.core.models import ClaimStatus
from daily_intel.intelligence.claims import check_numeric_claims, extract_claims
from daily_intel.intelligence.quality import AnalysisQualityGate, QualityPolicy
from daily_intel.publication.verification import verification_status
from test_intelligence_quality import _draft, _document, _verification, NOW
from test_remediation_sept12 import _claim


def test_supported_reporting_does_not_require_primary_source():
    gate = AnalysisQualityGate(QualityPolicy())
    draft = gate.normalize_draft(_draft())
    decision = gate.evaluate(draft, _verification(draft), [_document().model_copy(update={"source_tier": 2})])
    assert decision.deep
    assert decision.quality.final_content_passed
    assert decision.quality.source_limitations == ["missing_primary_source"]
    analysis = gate.build_analysis("e", decision, "fixture", "test", NOW)
    assert verification_status(analysis)["label"] == "已核对报道"


@pytest.mark.parametrize("field,complete", [("key_facts.3", True), ("technical_mechanism", False)])
def test_removed_claims_remain_in_audit_but_do_not_fail_final_content(field, complete):
    gate = AnalysisQualityGate(QualityPolicy())
    draft = gate.normalize_draft(_draft())
    verification = _verification(draft)
    rejected = next(c for c in verification.claim_checks if c.claim_id == field)
    rejected.status = "unsupported"
    verification.unsupported_claims = [rejected.text]
    verification.verdict = "downgrade"
    decision = gate.evaluate(draft, verification, [_document()])
    assert decision.quality.final_content_passed
    assert decision.deep == complete
    assert decision.quality.removed_claim_count == 1
    analysis = gate.build_analysis("e", decision, "fixture", "test", NOW)
    assert rejected.text not in [c.text for c in extract_claims(analysis)]
    assert any(c.text == rejected.text and c.status == ClaimStatus.UNSUPPORTED for c in analysis.quality.claim_audits)
    assert "刊登内容" in verification_status(analysis)["detail"]


def test_unlocalized_adverse_verdict_still_blocks_release():
    gate = AnalysisQualityGate(QualityPolicy())
    draft = gate.normalize_draft(_draft())
    verification = _verification(draft, verdict="downgrade")
    decision = gate.evaluate(draft, verification, [_document()])
    assert not decision.quality.final_content_passed
    assert not decision.deep


def test_missing_headline_or_rewritten_text_cannot_pass_final_content():
    gate = AnalysisQualityGate(QualityPolicy())
    draft = gate.normalize_draft(_draft())
    verification = _verification(draft)
    verification.claim_checks[0].status = "unsupported"
    assert not gate.evaluate(draft, verification, [_document()]).quality.final_content_passed
    draft.technical_mechanism = "Long mechanism " * 100
    result = gate.evaluate(draft, _verification(draft), [_document()])
    assert result.safe_draft.technical_mechanism == ""
    assert not result.deep
    assert result.quality.final_content_passed


@pytest.mark.parametrize("text", [
    "第一次演示时输入了不同的客户条件。",
    "第一次让Kairos测试技能时，它没有回复。",
    "不能据此推断长期领先。",
    "不能据此推断文献优先性或全面领先。",
    "论文将这一形式化列为贡献，但“首次”与否需以同行评审为准。",
    "论文自称“首次”端到端证明，但这是 to our knowledge 的声明，不是已核实的文献优先性。",
])
def test_operating_sequence_and_explicit_limitations_are_not_novelty_claims(text):
    result = check_numeric_claims([_claim(text, "The source describes the demonstration and its limitations.")], [_document()])
    assert result[0].status == ClaimStatus.SUPPORTED


@pytest.mark.parametrize("text", [
    "第一次演示后，首次提出新算法。",
    "不能据此推断长期领先，但准确率全球最高。",
    "首次提出新算法，但不能证明量产可行。",
])
def test_qualified_sentence_does_not_hide_positive_overclaim(text):
    result = check_numeric_claims([_claim(text, "We propose a new algorithm.")], [_document()])
    assert result[0].status != ClaimStatus.SUPPORTED
