import pytest

from daily_intel.core.models import ClaimStatus
from daily_intel.intelligence.claims import check_numeric_claims
from daily_intel.publication.verification import verification_status
from test_intelligence_quality import _document
from test_remediation_sept12 import _claim
from test_settings_and_publication import _reading_analysis


@pytest.mark.parametrize("issues,counts,state", [
    (["missing_primary_source"], (25, 0), "source_limited"),
    (["unsupported_claims"], (25, 1), "partial"),
    (["missing_required_sections"], (25, 0), "partial"),
    (["research_failed"], (0, 0), "incomplete"),
    (["broad_reading_only"], (0, 0), "unreviewed"),
    ([], (0, 0), "unreviewed"),
    (["unverifiable_claims"], (0, 1), "unsupported"),
])
def test_labels_distinguish_audit_from_publication_gate(issues, counts, state):
    a = _reading_analysis("e", "Title", "Summary")
    a.quality.issues = issues
    a.quality.claim_audits = [
        _claim(f"fact {i}", "source quote") for i in range(sum(counts))
    ]
    for c in a.quality.claim_audits[counts[0]:]:
        c.status = ClaimStatus.UNVERIFIABLE
    result = verification_status(a)
    assert result["state"] == state
    assert result["supported"] == counts[0]
    assert not a.quality.passed  # Display labels never upgrade the quality gate.


@pytest.mark.parametrize("text", ["该方法并非全面领先。", "发送吞吐方面Rustls未领先。", "结果不一定同比例领先。", "单核总分优势不一定等于所有真实负载都同比例领先。"])
def test_negative_ranking_is_not_positive_superiority(text):
    assert check_numeric_claims([_claim(text, "No overall advantage was demonstrated.")], [_document()])[0].status == ClaimStatus.SUPPORTED


def test_negative_clause_does_not_hide_a_positive_ranking():
    text = "并非全面领先，但在准确率上最高。"
    assert check_numeric_claims([_claim(text, "No overall advantage was demonstrated.")], [_document()])[0].status != ClaimStatus.SUPPORTED


def test_quick_read_omits_audit_labels_but_details_keep_them(tmp_path):
    from bs4 import BeautifulSoup
    from test_intelligence_quality import NOW
    from daily_intel.publication.reporting import publish
    a = _reading_analysis("e", "报道标题", "报道摘要")
    a.quality.issues = ["missing_primary_source"]
    a.quality.claim_audits = [_claim("fact", "source quote")]
    b = a.model_copy(deep=True)
    b.event_id = "b"
    b.quality.issues = ["research_failed"]
    b.quality.claim_audits = []
    context = {"title": "测试", "usage": {}, "intensive_analyses": [a], "extensive_analyses": [b]}
    path = publish(context, [a, b], {"run_name": "labels"}, tmp_path, NOW)["html"]
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    text = soup.get_text(" ", strip=True)
    assert "待核实线索" not in text
    assert "已核对报道 1 条" not in text
    assert "核验表示对照所供原文，不代表独立复现实验" not in text
    assert soup.select_one(".scan-item .badge") is None
    assert soup.select_one(".scan-item") is not None
    assert "已核对报道" not in soup.select_one(".digest").get_text()
    assert soup.select_one("#intensive-panel .story-kicker .badge").get_text(strip=True) == "已核对报道"
    assert soup.select_one(".extensive-detail .badge").get_text(strip=True) == "核验未完成"
    assert "缺少一手材料" in text
    assert list(path.parent.iterdir()) == [path]
