from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from daily_intel.core.models import ClaimAudit, ClaimStatus, Event, VerificationResult
from daily_intel.intelligence.claims import check_numeric_claims, extract_claims
from daily_intel.intelligence.modeling import ModelStageRunner
from daily_intel.intelligence.prompts import analyst_user, scout_user
from daily_intel.intelligence.quality import AnalysisQualityGate, QualityPolicy, exact_evidence_quote
from daily_intel.intelligence.snapshots import source_published_at
from daily_intel.intelligence.sources.publication_dates import explicit_publication_date
from daily_intel.intelligence.sources.sitemaps import SitemapSource
from daily_intel.publication.reporting import publish
from test_intelligence_quality import NOW, _document, _draft, _verification
from test_sources_and_clustering import FakeResponse


def _claim(text, quote):
    return ClaimAudit(claim_id="headline", field_path="headline", text=text,
                      status=ClaimStatus.SUPPORTED, document_id="doc-1", quote=quote)


@pytest.mark.parametrize("text,quote", [
    ("构建和捕获成本在首次求解内收回。", "Tree construction and graph capture pay for themselves within the first solve."),
    ("Energinet允许负荷爬坡最高60 MW/min。", "Energinet allows ramping up to 60 MW/min."),
])
def test_operating_conditions_are_not_novelty_or_ranking(text, quote):
    doc = _document().model_copy(update={"content": quote, "summary": quote})
    assert check_numeric_claims([_claim(text, quote)], [doc])[0].status == ClaimStatus.SUPPORTED


@pytest.mark.parametrize("text,quote", [
    ("首次提出新算法。", "We propose a new algorithm."),
    ("最高提升12.6个百分点。", "One task improved by 12.6 points, another by 24.6 points."),
    ("夜间仍能跟踪，说明系统可全天候工作。", "LED propellers enable tracking even in the dark."),
    ("雷达新视角合成精度达此前最优方法的1.7倍。", "Mean Pearson correlation is 0.587, 1.7 times the baseline on six outdoor scenes."),
])
def test_overstated_claims_still_fail_even_when_verifier_passes(text, quote):
    doc = _document().model_copy(update={"content": quote, "summary": quote})
    assert check_numeric_claims([_claim(text, quote)], [doc])[0].status != ClaimStatus.SUPPORTED


def test_correlated_metric_with_scope_is_preserved():
    quote = "On six outdoor scenes, mean Pearson correlation is 0.587, 1.7 times RadarSplat."
    text = "在六个户外场景中，平均皮尔逊相关系数为0.587，是RadarSplat的1.7倍。"
    doc = _document().model_copy(update={"content": quote, "summary": quote})
    assert check_numeric_claims([_claim(text, quote)], [doc])[0].status == ClaimStatus.SUPPORTED


def test_explicit_weather_limitation_is_not_an_all_weather_claim():
    quote = "Only nighttime tracking has been demonstrated."
    doc = _document().model_copy(update={"content": quote, "summary": quote})
    text = "夜间实验尚不能证明系统可全天候工作。"
    assert check_numeric_claims([_claim(text, quote)], [doc])[0].status == ClaimStatus.SUPPORTED


def test_date_parser_ignores_modification_and_related_article_dates():
    html = '''<meta property="article:modified_time" content="2026-09-11">
    <script type="application/ld+json">{"@type":"Article","datePublished":"2025-12-18",
    "citation":{"@type":"Article","datePublished":"2020-01-01"}}</script>'''
    assert explicit_publication_date(html) == datetime(2025, 12, 18, tzinfo=timezone.utc)
    assert explicit_publication_date('<meta property="article:modified_time" content="2026-09-11">') is None
    assert explicit_publication_date('<meta property="article:published_time" content="invalid">') is None


def test_sitemap_keeps_publication_and_update_dates_separate(monkeypatch):
    sitemap = b'<urlset><url><loc>https://example.com/research/bloom</loc><lastmod>2026-09-10</lastmod></url></urlset>'
    page = '<meta property="article:published_time" content="2025-12-18"><title>Bloom</title>'
    monkeypatch.setattr("daily_intel.intelligence.sources.sitemaps.requests.get",
                        lambda url, **kw: FakeResponse(sitemap) if url.endswith(".xml") else FakeResponse(page.encode(), page))
    source = SitemapSource({"id": "official", "name": "Official", "tier": 1,
                            "url": "https://example.com/sitemap.xml", "fetch_page_metadata": True})
    doc = source.collect(datetime(2026, 9, 9, tzinfo=timezone.utc), 10)[0]
    assert doc.published_at.year == 2025
    assert doc.metadata["source_updated_at"].startswith("2026-09-10")
    assert source_published_at(doc).year == 2025
    legacy = doc.model_copy(update={"metadata": {"sitemap_url": "https://example.com/sitemap.xml"}})
    assert source_published_at(legacy) is None
    event = Event(id="e", title="Bloom", topic_id="x", topic_name="x", document_ids=[doc.id],
                  first_seen=NOW, last_seen=NOW, source_quality=80, deterministic_score=80)
    assert json.loads(analyst_user(event, [legacy]))["documents"][0]["published_at"] is None
    assert json.loads(scout_user([(event, [legacy])], []))["events"][0]["sources"][0]["published_at"] is None


def test_duplicate_sitemap_date_repair_preserves_historical_snapshot(tmp_path):
    from daily_intel.infrastructure.storage.sqlite import SQLiteIntelligenceRepository
    repo = SQLiteIntelligenceRepository(tmp_path / "test.db")
    doc = _document().model_copy(update={"metadata": {"sitemap_url": "https://example.com/sitemap.xml"}})
    repo.upsert_document(doc)
    old = repo.save_content_snapshot("historical input remains intact")
    published = datetime(2025, 12, 18, tzinfo=timezone.utc)
    repaired = doc.model_copy(update={"published_at": published, "metadata": {
        **doc.metadata, "date_basis": "published", "original_published_at": published.isoformat(),
        "source_updated_at": NOW.isoformat(),
    }})
    assert repo.upsert_document(repaired) is False
    assert repo.recent_documents(datetime(2026, 8, 24, tzinfo=timezone.utc)) == []
    assert repo.get_documents([doc.id])[0].content == doc.content
    assert repo.get_content_snapshot(old) == "historical input remains intact"


def test_cross_midnight_generation_uses_completion_time_and_start_date(tmp_path, monkeypatch):
    import pandas as pd
    from pathlib import Path
    from daily_intel.app import orchestrator
    from daily_intel.core.settings import load_settings
    from daily_intel.github.pipeline import GitRunResult
    from daily_intel.infrastructure.storage.sqlite import SQLiteIntelligenceRepository
    from daily_intel.intelligence.pipeline import IntelligenceRunResult
    from daily_intel.market.pipeline import MarketRunResult

    started = datetime.fromisoformat("2026-09-11T14:13:38+08:00")
    finished = datetime.fromisoformat("2026-09-12T01:23:14+08:00")

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return finished.astimezone(tz)

    monkeypatch.setattr(orchestrator, "datetime", Clock)
    monkeypatch.setattr(orchestrator, "install_proxy_fallback", lambda: None)
    settings = load_settings(Path(__file__).parents[1] / "config/settings.yaml")
    captured = {}

    def render(context, analyses, metadata, output_dir, now):
        captured.update(metadata)
        return publish(context, analyses, metadata, tmp_path / "output", now)

    output = orchestrator.run_application(
        settings, now=started, repository=SQLiteIntelligenceRepository(tmp_path / "test.db"),
        market_workflow=SimpleNamespace(run=lambda: MarketRunResult(
            radar_news=pd.DataFrame(), context={"market_source_status": [], "market_date": "2026-09-11"}, metadata={})),
        intelligence_workflow=SimpleNamespace(run=lambda *a, **kw: IntelligenceRunResult(
            analyses=[], source_status=[], ai_status="disabled", usage={})),
        github_workflow=SimpleNamespace(run=lambda *a: GitRunResult(projects=[], source_status=[], errors=[])),
        publisher=SimpleNamespace(publish=render),
    )["html"]
    assert output.parents[2].name == "2026-09-11"
    assert captured["report_date"] == "2026-09-11"
    assert captured["generated_at"] == finished.isoformat()
    assert captured["started_at"] == started.isoformat()
    assert "生成于 2026-09-12 01:23:14" in output.read_text(encoding="utf-8")


def test_downgrade_preserves_supported_sections_and_renders_safe_html(tmp_path):
    gate = AnalysisQualityGate(QualityPolicy())
    draft = gate.normalize_draft(_draft())
    verification = _verification(draft)
    for c in verification.claim_checks:
        if c.claim_id in {"headline", "risks.0"}:
            c.status = "unsupported"
            c.notes = "This claim exceeds the supplied evidence."
    decision = gate.evaluate(draft, verification, [_document()])
    analysis = gate.build_analysis("e", decision, "fixture", "v", NOW, source_headline="MindTopo benchmark")
    assert not analysis.quality.passed
    assert analysis.technical_mechanism == draft.technical_mechanism
    assert draft.risks[0] not in analysis.risks
    assert any(c.field_path == "risks[0]" for c in extract_claims(draft))
    context = {"title": "Test", "report_date": "2026-09-11", "market_date": "2026-09-11",
               "usage": {}, "intensive_analyses": [analysis], "extensive_analyses": [analysis],
               "github_projects": [{"full_name": "demo/tool", "plain": "日期输入示例",
                                    "use_cases": ['Use <input type="date"> and <important if>.']} ]}
    path = publish(context, [analysis], {"run_name": "regression"}, tmp_path, NOW)["html"]
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    assert not soup.select("input, important")
    assert '<input type="date">' in soup.get_text()
    assert "暂无可靠解读" in soup.select_one(".story-title").get_text()
    assert "原文题名：MindTopo benchmark" in soup.select_one("details.deep-dive").get_text()
    assert "This claim exceeds the supplied evidence." in soup.get_text()
    assert "草稿表述未采用" in soup.get_text()
    assert "具体怎么做" in soup.get_text()
    assert analysis.technical_mechanism in soup.get_text()
    assert "已核验" not in soup.select_one(".story-kicker").get_text()
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("repair", [True, False])
def test_quote_correction_is_bounded_and_preserves_adverse_verdict(monkeypatch, repair):
    doc = _document().model_copy(update={"content": "Alpha first part.\nPage 2\nFigure 1 data.\nBeta second part.", "summary": ""})
    assert exact_evidence_quote("Alpha first part. Beta second part.", doc) is None
    event = Event(id="e", title="E", topic_id="x", topic_name="x", document_ids=[doc.id],
                  first_seen=NOW, last_seen=NOW, source_quality=80, deterministic_score=80)
    result = VerificationResult(verdict="downgrade", confidence_adjustment=0, notes="checked", claim_checks=[
        {"claim_id": "headline", "status": "supported", "document_id": doc.id,
         "quote": "Alpha first part. Beta second part."},
        {"claim_id": "risks.0", "status": "unsupported", "notes": "Unproven risk"},
    ])
    revised = result.model_copy(deep=True)
    revised.verdict = "pass"
    revised.claim_checks[1].status = "supported"
    if repair:
        revised.claim_checks[0].quote = "Alpha first part."
    runner = ModelStageRunner({"llm": {"prompt_version": "test"}, "intelligence": {}}, SimpleNamespace(), SimpleNamespace(), QualityPolicy())
    calls = []

    def generate(stage, system, user, *args):
        payload = json.loads(user)
        calls.append(payload)
        return SimpleNamespace(value=result if len(calls) == 1 else revised)

    monkeypatch.setattr(runner, "_generate", generate)
    monkeypatch.setattr(runner, "audit", lambda *a, **kw: None)
    checked = runner.verify(event, [doc], _draft())
    assert len(calls) == 2
    assert calls[0]["documents"] == calls[1]["documents"]
    assert checked.verdict == "downgrade"
    assert checked.claim_checks[1].status == "unsupported"
    assert (exact_evidence_quote(checked.claim_checks[0].quote, doc) is not None) == repair


def test_cross_midnight_email_selects_this_run_and_never_sends_stale_success(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("send_report_test", Path(__file__).parents[1] / "scripts/send_report.py")
    sender = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sender)
    monkeypatch.setattr(sender, "PROJECT_ROOT", tmp_path)
    report = tmp_path / "output/2026-09-11/runs/141338-test/daily_digest.html"
    report.parent.mkdir(parents=True)
    report.write_text("<h2>今日速读</h2><p>正确报告</p>", encoding="utf-8")
    today = tmp_path / "output/2026-09-12/runs/010000-other/daily_digest.html"
    today.parent.mkdir(parents=True)
    today.write_text("Other run", encoding="utf-8")
    log = tmp_path / "agent.log"
    log.write_text(f"生成完成：\n  html: {report}\n", encoding="utf-8")
    env = dict.fromkeys(["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "REPORT_TO"], "fixture")
    monkeypatch.setattr(sender, "load_env", lambda: env)
    sent = []
    monkeypatch.setattr(sender, "send", lambda *args: sent.append(args))
    args = ["--run-log", str(log), "--report-date", "2026-09-11"]
    assert sender.main(args + ["--run-exit-code", "0"]) == 0
    assert "2026-09-11" in sent[-1][1]
    assert sent[-1][3][0][1] == report.read_bytes()
    assert sender.main(args + ["--run-exit-code", "2"]) == 1
    assert not sent[-1][3]
    log.write_text("Interrupted before publication", encoding="utf-8")
    assert sender.main(args) == 1
    assert not sent[-1][3]
