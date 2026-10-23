import pandas as pd
import pytest
from bs4 import BeautifulSoup

from daily_intel.publication.reporting import publish
from test_intelligence_pipeline import _broad_pipeline, NOW
from test_settings_and_publication import _reading_analysis


def test_broad_scan_survives_pipeline_separately_from_summary(tmp_path, monkeypatch):
    pipeline, _ = _broad_pipeline(tmp_path, monkeypatch)
    generate = pipeline.stages.llm.generate

    def with_summary(stage, system, user, schema):
        result = generate(stage, system, user, schema)
        if stage == "scout_rerank":
            for item in result.value.items:
                item.public_summary = "研究团队尝试减少工具的运行等待。材料报告了实验结果，实际使用仍需验证。"
        return result

    monkeypatch.setattr(pipeline.stages.llm, "generate", with_summary)
    result = pipeline.run(NOW, pd.DataFrame(), require_ai=True)
    leads = [a for a in result.analyses if "broad_reading_only" in a.quality.issues]
    assert leads
    for lead in leads:
        supplied = pipeline.selector.scan_details(lead.event_id)
        assert supplied["scan"] != supplied["public_summary"]
        assert lead.scan_sentence == supplied["scan"]
        assert lead.plain_takeaway == supplied["public_summary"]
        assert not lead.quality.passed
        assert pipeline.repository.get_analysis(lead.event_id, result.cache_scope) is None


def test_reading_layout_keeps_numbers_qualifiers_quotes_and_audit(tmp_path):
    text = "工具升级到 v0.6.0，耗时从 2.85 秒降到 1.63 秒。仅限作者的测试环境，尚未验证日常使用效果。"
    a = _reading_analysis("decimal", "工具在指定测试中减少等待", text)
    a.scan_sentence = "作者在指定测试中让工具运行得更快，日常使用效果尚待验证。"
    a.key_facts = ["数值对照：2.85 秒与 1.63 秒。"]
    before = a.model_dump_json()
    context = {"title": "试读", "usage": {}, "intensive_analyses": [a]}
    html = publish(context, [a], {"run_name": "readable"}, tmp_path, NOW)["html"]
    soup = BeautifulSoup(html.read_text(encoding="utf-8"), "html.parser")
    story = soup.select_one("#intensive-panel article.story")
    paragraphs = story.select(".reader-summary p")
    assert "".join(p.get_text() for p in paragraphs) == text
    assert len(paragraphs) == 2
    assert story.select_one(".badge").get_text(strip=True) == "未做逐项核验"
    details = story.select_one("details.deep-dive")
    assert "open" not in details.attrs
    assert a.key_facts[0] in story.select_one(".reader-facts").get_text()
    assert story.select_one(".reader-facts").find_parent("details") is details
    assert all(e.quote in details.get_text() for e in a.evidence)
    assert a.model_dump_json() == before


def test_placeholder_does_not_promote_raw_english_to_plain_summary(tmp_path):
    a = _reading_analysis("placeholder", "待核验：来源材料", "")
    a.publication.unverified_placeholder = True
    a.publication.source_headline = "A dense technical research title"
    a.publication.source_excerpts = ["Unverified English abstract with benchmark claims."]
    context = {"title": "试读", "usage": {}, "intensive_analyses": [], "extensive_analyses": [a]}
    path = publish(context, [a], {"run_name": "placeholder"}, tmp_path, NOW)["html"]
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    detail = soup.select_one(".extensive-detail")
    assert a.publication.source_excerpts[0] in detail.select_one("details.deep-dive").get_text()
    assert a.publication.source_headline in detail.select_one("details.deep-dive").get_text()
    assert "暂无可靠解读" in detail.select_one("h2").get_text()
    assert "Unverified English" not in detail.select_one(".takeaway").get_text()
    assert "暂缺可靠的中文解读" in soup.select_one(".digest").get_text()


@pytest.mark.parametrize("lane, selector", [
    ("intensive", "#intensive-panel article.story"),
    ("extensive", ".extensive-detail"),
])
def test_summary_keeps_scope_while_full_results_and_limits_expand(tmp_path, lane, selector):
    summary = "论文研究如何缩小语音合成模型。结果仅限单声音实验，不能推断所有设备速度。"
    a = _reading_analysis("conditions", "Paradee：小体积语音合成模型", summary)
    a.scan_sentence = "论文报告单声音语音合成实验。"
    # Long facts must not lose the qualification at their end during rendering.
    a.key_facts = ["实验记录。" * 110 + "仅限单声音模型，教师模型支持54种声音。"]
    a.maturity = "论文实验，代码与模型文件已公开。"
    a.risks = ["速度来自作者单CPU线程测试，不能推断所有设备速度。"]
    a.counterpoints = ["UTMOS为4.41，低于教师的4.52。"]
    before = a.model_dump_json()
    context = {"title": "修订", "usage": {}, "intensive_analyses": [], "extensive_analyses": []}
    context[f"{lane}_analyses"] = [a]
    path = publish(context, [a], {"run_name": lane}, tmp_path, NOW)["html"]
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    card = soup.select_one(selector)
    facts = card.select_one(".reader-facts")
    details = card.select_one("details.deep-dive")
    assert facts.find_parent("details") is details
    assert "open" not in details.attrs
    assert "".join(p.get_text() for p in card.select(".reader-summary p")) == summary
    assert card.select_one(".reader-summary").find_parent("details") is None
    for value in a.key_facts + [a.maturity] + a.risks + a.counterpoints:
        assert value in facts.get_text()
    assert all(e.quote in card.select_one("details.deep-dive").get_text() for e in a.evidence)
    assert a.model_dump_json() == before


def test_source_summary_counts_events_and_discloses_coverage_gaps(tmp_path):
    a = _reading_analysis("paper", "研究结果", "仅供线索参考。")
    a.evidence[0].url = "https://arxiv.org/abs/example"
    a.evidence.append(a.evidence[0].model_copy())
    a.evidence.append(a.evidence[0].model_copy(update={"url": "http://[invalid"}))
    context = {"title": "修订", "usage": {}, "intelligence_source_status": [
        {"name": "failed-feed", "stale": True},
    ]}
    path = publish(context, [a], {"run_name": "sources", "freshness": {
        "failed_or_cached_sources": ["failed-feed"],
    }}, tmp_path, NOW)["html"]
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    summary = soup.select_one(".source-summary")
    assert summary.find_parent(attrs={"role": "tabpanel"}) is None
    assert "arxiv.org · 1 条信息" in summary.get_text(" ", strip=True)
    assert summary.get_text().count("failed-feed") == 1
    assert "内容偏重研究论文" in summary.get_text()
