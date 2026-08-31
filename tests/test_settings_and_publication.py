from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from daily_intel.app.orchestrator import run_application
from daily_intel.core.models import Analysis, AnalysisQuality, AnalysisStatus, Evidence
from daily_intel.core.settings import _validate_sources, load_settings, resolve_path
from daily_intel.intelligence.sources.factory import configured_source_count
from daily_intel.infrastructure.storage.sqlite import SQLiteIntelligenceRepository
from daily_intel.intelligence.pipeline import IntelligenceRunResult
from daily_intel.market.pipeline import MarketRunResult
from daily_intel.publication.reporting import publish


def test_project_config_resolves_expected_paths_and_sources() -> None:
    root = Path(__file__).resolve().parents[1]
    settings = load_settings(root / "config" / "settings.yaml")
    assert resolve_path(settings, "cache_dir") == (root / "data" / "cache").resolve()
    assert configured_source_count(settings["sources"]) >= 30
    assert len(settings["sources"]["arxiv_sources"]) == 3
    assert settings["llm"]["prompt_version"] == "tech-intel-v3"
    assert settings["quality"]["policy_version"] == "evidence-gate-v2"


def test_source_config_rejects_duplicate_ids_and_unknown_api_types() -> None:
    with pytest.raises(ValueError, match="重复来源 id"):
        _validate_sources({
            "feeds": [
                {"id": "same", "url": "https://example.com/a", "tier": 1},
                {"id": "same", "url": "https://example.com/b", "tier": 2},
            ]
        })
    with pytest.raises(ValueError, match="不支持的 API 类型"):
        _validate_sources({
            "apis": [{"id": "unknown", "type": "unknown", "tier": 1}]
        })


def test_publish_writes_unified_outputs(tmp_path) -> None:
    now = datetime(2026, 8, 24, 10, tzinfo=timezone.utc)
    analysis = Analysis(
        event_id="event-1", status=AnalysisStatus.DEEP, headline="技术深研",
        plain_takeaway="实验室公布了一项可核对的工程改进，短时间内还不会大规模落地。",
        key_facts=["第一条核心事实", "第二条核心事实", "第三条补充事实"],
        technical_mechanism="这里解释技术机制。", novelty="这里说明新颖性。",
        maturity="原型验证阶段。", outlook_6_24m="未来影响仍取决于工程验证。",
        risks=["尚未经过大规模部署"], counterpoints=["现有方案仍有成本优势"],
        confidence=.82,
        evidence=[
            Evidence(
                document_id="doc-1", url="https://example.com/source",
                quote="This is a sufficiently long source quotation.", locator="原文第一段",
            ),
            Evidence(
                document_id="doc-2", url="https://example.com/second-source",
                quote="This is a second sufficiently long source quotation.", locator="原文第二段",
            ),
        ],
        quality=AnalysisQuality(
            policy_version="evidence-gate-v1", passed=True, score=100,
            supported_evidence=2, primary_sources=2, source_diversity=2,
        ),
        model="fixture-model", prompt_version="test-v1", created_at=now,
    )
    context = {
        "title": "测试日报", "report_date": "2026-08-24", "market_date": "2026-08-24",
        "generated_at": "2026-08-24 18:10 CST", "is_trading_day": True,
        "analyses": [analysis], "ai_status": "disabled", "ai_status_label": "AI未启用",
        "usage": {"calls": 0, "input_tokens": 0, "output_tokens": 0},
        "prompt_version": "test-v1", "pipeline_errors": [],
        "news_records": [{
            "title": "商务部宣布对半导体设备实施出口管制",
            "summary": "该政策可能改变相关产业链交易预期。",
            "url": "https://example.com/brief", "published_at": "2026-08-24 09:00",
            "tags": "制裁",
        }],
        "market_source_status": [], "intelligence_source_status": [],
        "github_projects": [{
            "full_name": "huggingface/transformers",
            "url": "https://github.com/huggingface/transformers",
            "kicker": "软件",
            "plain": "这是一个开源机器学习库，用来加载和运行各种大模型。",
            "language": "Python",
            "origin": "github",
            "origin_label": "GitHub",
            "reason": "今日最热",
            "stars_today": 1200,
            "stars_week": 5000,
            "stars_total": 150000,
            "stars_total_label": "15万",
            "rank": 1,
            "today_width": 100,
            "week_width": 100,
        }],
    }
    metadata = {
        "run_name": "100000-test",
        "experiment_id": "test",
        "ai": {"status": "disabled"},
    }
    outputs = publish(
        context, [analysis], metadata,
        tmp_path, now,
    )
    assert set(outputs) == {"html"}
    html = outputs["html"].read_text(encoding="utf-8")
    assert 'role="tab"' in html
    assert 'id="news-panel"' in html
    assert 'id="market-panel"' in html and 'aria-labelledby="market-tab" hidden' in html
    assert '<details class="deep-dive">' in html
    assert 'href="https://example.com/source"' in html
    assert "阅读原文" in html and "出口管制" in html
    assert "今日速读" in html
    assert "硬核" in html
    assert "市场情报" in html
    assert 'data-tab="news">科技</button>' in html
    assert 'data-subtab="general"' in html and 'data-subtab="hardcore"' in html
    assert "新闻精选" not in html
    digest_html = html.split('id="news-panel"', 1)[0]
    assert "实验室公布了一项可核对的工程改进，短时间内还不会大规模落地。" in digest_html
    digest_body = digest_html.split("<h2>今日速读</h2>", 1)[1]
    assert "硬核" in digest_body
    assert "scan-kicker" in digest_body
    assert "技术深研" in html
    assert "赚钱效应" not in html
    market_html = html.split('id="market-panel"', 1)[1]
    assert "产业风向" not in html and "全球市场" not in html
    assert "金融行业" not in market_html
    assert "纳斯达克" not in market_html and "黄金" not in market_html
    assert "scan-kicker" in market_html
    assert "影响与后果" not in market_html and "依据" not in market_html
    assert "可归因事件" not in html
    assert "可能影响" not in market_html and "推理过程" not in market_html
    assert "深</span>" not in digest_html and ">线索<" not in digest_html
    assert "出口管制" in html
    assert "个股扫描" not in html
    assert "长江证券" not in html
    assert 'id="git-panel"' in html
    assert "huggingface/transformers" in html
    git_html = html.split('id="git-panel"', 1)[1].split('id="market-panel"', 1)[0]
    assert 'class="story"' in git_html
    assert "语言与热度" not in git_html
    assert "使用场景模拟" not in git_html
    assert "打开仓库" in git_html and "trend-fill" in git_html
    assert "共 15万 星" in git_html
    assert "推演" not in git_html
    collapsed, expanded = html.split('<details class="deep-dive">', 1)
    assert "第一条核心事实" in collapsed and "第二条核心事实" in collapsed
    assert "第三条补充事实" not in collapsed and "第三条补充事实" in expanded
    assert outputs["html"].parent == tmp_path / "2026-08-24" / "runs" / "100000-test"
    assert not (tmp_path / "2026-08-24" / "daily_digest.html").exists()
    assert not (tmp_path / "2026-08-24" / "latest_run.json").exists()
    assert list(outputs["html"].parent.glob("*")) == [outputs["html"]]


def test_publish_preserves_multiple_same_day_runs(tmp_path) -> None:
    now = datetime(2026, 8, 24, 10, tzinfo=timezone.utc)
    context = {
        "title": "测试", "report_date": "2026-08-24", "market_date": "2026-08-24",
        "generated_at": "2026-08-24 10:00 CST", "is_trading_day": True,
        "analyses": [], "ai_status": "disabled", "ai_status_label": "AI未启用",
        "usage": {"calls": 0, "input_tokens": 0, "output_tokens": 0},
        "prompt_version": "test", "pipeline_errors": [],
        "news_records": [],
        "market_source_status": [], "intelligence_source_status": [],
    }
    first = publish(
        {**context, "run_name": "run-a", "experiment_id": "model-a"},
        [],
        {"run_name": "run-a", "experiment_id": "model-a"}, tmp_path, now,
    )
    second = publish(
        {**context, "run_name": "run-b", "experiment_id": "model-b"},
        [],
        {"run_name": "run-b", "experiment_id": "model-b"}, tmp_path, now,
    )
    assert first["html"].exists() and second["html"].exists()
    assert first["html"] != second["html"]
    assert not (tmp_path / "2026-08-24" / "latest_run.json").exists()


def test_orchestrator_uses_injected_workflows_publisher_and_actual_model_metadata(
    tmp_path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    settings = load_settings(root / "config" / "settings.yaml")
    now = datetime(2026, 8, 25, 10, tzinfo=timezone.utc)
    market_result = MarketRunResult(
        radar_news=pd.DataFrame(),
        context={
            "market_date": "2026-08-25",
            "is_trading_day": True,
            "news_records": [],
            "market_source_status": [],
        },
        metadata={"market_date": "2026-08-25"},
    )
    intelligence_result = IntelligenceRunResult(
        analyses=[],
        source_status=[],
        ai_status="enabled",
        usage={
            "calls": 1,
            "input_tokens": 100,
            "output_tokens": 50,
            "estimated": True,
        },
        model_runtime={
            "provider": "qwen-code-agent",
            "models": {"analyst": "qwen-code-agent"},
            "usage_reporting": "estimated",
        },
        quality_summary={
            "policy_version": "evidence-gate-v1",
            "average_score": 0,
            "passed": 0,
            "downgraded": 0,
            "issue_counts": {},
        },
    )

    class MarketWorkflow:
        def run(self):
            return market_result

    class IntelligenceWorkflow:
        def run(self, *args, **kwargs):
            return intelligence_result

    class CapturingPublisher:
        metadata = None

        def publish(self, context, analyses, metadata, output_dir, now):
            self.metadata = metadata
            return {"custom": tmp_path / "custom-output"}

    class GitWorkflow:
        def run(self, *args, **kwargs):
            from daily_intel.github.pipeline import GitRunResult
            return GitRunResult(projects=[], source_status=[], errors=[])

    publisher = CapturingPublisher()
    outputs = run_application(
        settings,
        now=now,
        repository=SQLiteIntelligenceRepository(tmp_path / "intelligence.db"),
        market_workflow=MarketWorkflow(),
        intelligence_workflow=IntelligenceWorkflow(),
        github_workflow=GitWorkflow(),
        publisher=publisher,
    )
    assert outputs == {"custom": tmp_path / "custom-output"}
    assert publisher.metadata["ai"]["provider"] == "qwen-code-agent"
    assert publisher.metadata["ai"]["usage_reporting"] == "estimated"
    assert publisher.metadata["intelligence"]["quality"]["policy_version"] == "evidence-gate-v1"


def test_plain_digest_scans_short_lines_and_skips_news_copy() -> None:
    from daily_intel.publication.plain_digest import build_plain_digest

    analysis = Analysis(
        event_id="event-1", status=AnalysisStatus.LEAD, headline="技术深研",
        plain_takeaway="实验室公布了一项可核对的工程改进，短时间内还不会大规模落地。",
        key_facts=["这项改进让长文推理更省计算。", "仍需独立复现。"],
        confidence=.4,
        quality=AnalysisQuality(policy_version="evidence-gate-v2", passed=False, score=40),
        model="fixture-model", prompt_version="tech-intel-v3",
        created_at=datetime(2026, 8, 24, 10, tzinfo=timezone.utc),
    )
    digest = build_plain_digest([analysis], {})
    assert digest["has_content"]
    assert digest["tech_items"][0]["kicker"] == "科技"
    assert digest["hardcore_items"][0]["kicker"] == "科技"
    assert digest["general_items"] == []
    assert digest["tech_items"][0]["scan"] == "实验室公布了一项可核对的工程改进，短时间内还不会大规模落地。"
    assert "…" not in digest["tech_items"][0]["scan"]
    assert "news_threads" not in digest
    assert "industry_bars" not in digest and "board" not in digest
    from daily_intel.publication.briefing import apply_digest_brief
    news = apply_digest_brief(
        None,
        {"news_records": [{"title": "商务部宣布对半导体设备实施出口管制", "summary": "政策落地。"}]},
    )
    assert news[0]["kicker"] == "政策"
    assert "出口管制" in news[0]["scan"] or news[0]["scan"] == "政策落地。"

    robot = analysis.model_copy(update={
        "headline": "多臂机器人协作",
        "plain_takeaway": "新方法让多个机械臂在没见过的任务里也能分工。",
    })
    robot_digest = build_plain_digest([robot], {})
    assert robot_digest["tech_items"][0]["kicker"] == "机器人"

    transformers = analysis.model_copy(update={
        "headline": "Hugging Face Transformers v5.16.0发布：新增Qwen4-Exp等模型",
        "plain_takeaway": "开源机器学习库Transformers发布了新版本，新增了多个大模型，其中最受关注的是Qwen4-Exp，它把两种不同的注意力机制结合起来，让长文本处理更快更省内存。",
        "key_facts": [
            "ESMFold2采用迭代扩散方法进行蛋白质折叠预测，生物计算精度提升明显。",
        ],
    })
    transformers_digest = build_plain_digest([transformers], {})
    assert transformers_digest["tech_items"][0]["kicker"] in {"软件", "模型"}
    assert transformers_digest["tech_items"][0]["kicker"] != "生物"

    safety_tradeoff = analysis.model_copy(update={
        "headline": "TOTVS分享为AI代理构建数据层：用数据网格和语义模型降低token开销",
        "plain_takeaway": "企业要在精确性、安全性和成本之间平衡确定性逻辑与不可预测的大语言模型。",
    })
    assert build_plain_digest([safety_tradeoff], {})["tech_items"][0]["kicker"] == "模型"

    pytorch = analysis.model_copy(update={
        "headline": "PyTorch修复dropout在复数输入上的行为",
        "plain_takeaway": "PyTorch的dropout此前在CPU和CUDA上遇到复数输入会直接报错，而在苹果MPS芯片上却静默运行但结果未定义。",
    })
    assert build_plain_digest([pytorch], {})["tech_items"][0]["kicker"] == "软件"

    fingerprint = analysis.model_copy(update={
        "headline": "购物网站被曝用无声音频识别设备",
        "plain_takeaway": "该站点会在后台播放人耳听不到的音频，这属于一种设备指纹技术。",
    })
    assert build_plain_digest([fingerprint], {})["tech_items"][0]["kicker"] == "安全"
