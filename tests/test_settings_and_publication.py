from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from daily_intel.app.orchestrator import run_application
from daily_intel.core.models import (
    Analysis, AnalysisQuality, AnalysisStatus, Evidence,
    GitBriefingBatch, GitProjectBrief,
)
from daily_intel.core.settings import _validate_sources, load_settings, resolve_path
from daily_intel.intelligence.sources.factory import configured_source_count
from daily_intel.infrastructure.storage.sqlite import SQLiteIntelligenceRepository
from daily_intel.intelligence.pipeline import IntelligenceRunResult
from daily_intel.intelligence.reading import (
    evidence_material_chars,
    partition_reading_analyses,
)
from daily_intel.market.pipeline import MarketRunResult
from daily_intel.publication.plain_digest import group_analyses_by_topic
from daily_intel.publication.reporting import group_scan_items, publish


def test_project_config_resolves_expected_paths_and_sources() -> None:
    root = Path(__file__).resolve().parents[1]
    settings = load_settings(root / "config" / "settings.yaml")
    assert resolve_path(settings, "cache_dir") == (root / "data" / "cache").resolve()
    assert configured_source_count(settings["sources"]) >= 30
    assert len(settings["sources"]["arxiv_sources"]) == 3
    anthropic = next(
        item for item in settings["sources"]["sitemaps"]
        if item["id"] == "anthropic_official"
    )
    assert "/claude-" in anthropic["include_paths"]
    assert anthropic["path_topic_hints"]["/claude-"] == "foundation_models"
    disabled = {
        item["id"] for item in settings["sources"]["feeds"]
        if not item.get("enabled", True)
    }
    assert {"hacker_newsletter", "tldr_ai"} <= disabled
    assert settings["llm"]["prompt_version"] == "tech-intel-v5"
    assert settings["quality"]["policy_version"] == "evidence-gate-v2"
    assert settings["intelligence"]["intensive_reading_events"] == 10


def test_group_scan_items_preserves_topic_and_scout_order() -> None:
    items = [
        {"kicker": "芯片", "scan": "芯片事件一"},
        {"kicker": "模型", "scan": "模型事件"},
        {"kicker": "芯片", "scan": "芯片事件二"},
    ]
    groups = group_scan_items(items)
    assert [group["label"] for group in groups] == ["芯片", "模型"]
    assert [item["scan"] for item in groups[0]["items"]] == [
        "芯片事件一", "芯片事件二",
    ]


def _reading_analysis(
    event_id: str, headline: str, takeaway: str, quotes: list[str] | None = None,
) -> Analysis:
    quotes = quotes or ["A" * 100, "B" * 100]
    return Analysis(
        event_id=event_id,
        status=AnalysisStatus.LEAD,
        headline=headline,
        plain_takeaway=takeaway,
        confidence=.45,
        evidence=[
            Evidence(
                document_id=f"{event_id}-doc", url=f"https://example.com/{event_id}",
                quote=quote, locator="正文",
            )
            for quote in quotes
        ],
        quality=AnalysisQuality(
            policy_version="evidence-gate-v2", passed=False, score=50,
            supported_evidence=len(quotes), primary_sources=0, source_diversity=1,
        ),
        model="fixture-model", prompt_version="test-v1",
        created_at=datetime(2026, 8, 24, 10, tzinfo=timezone.utc),
    )


def test_intensive_material_gate_demotes_thin_source_and_backfills() -> None:
    first = _reading_analysis("first", "大模型发布新架构", "模型速读甲。")
    thin_body = (
        "8月31日，智界RX安全科技全解读直播中，智界RX产品经理透露，"
        "智界RX已开展L3级自动驾驶准入测试。（人民财讯）"
    )
    thin = _reading_analysis(
        "thin", "智界RX已开展L3级自动驾驶准入测试", "汽车速读。",
        ["智界RX已开展L3级自动驾驶准入测试", thin_body],
    )
    replacement = _reading_analysis("replacement", "GPU芯片发布", "芯片速读。")

    intensive, extensive = partition_reading_analyses(
        [first, thin, replacement], 2
    )

    assert evidence_material_chars(thin) == len(thin_body)
    assert [item.event_id for item in intensive] == ["first", "replacement"]
    assert [item.event_id for item in extensive] == ["thin"]


def test_publish_groups_same_topics_in_quick_and_intensive_reading(tmp_path) -> None:
    model_one = _reading_analysis("model-one", "大模型架构甲", "模型速读甲。")
    chip = _reading_analysis("chip", "GPU芯片发布", "芯片速读。")
    model_two = _reading_analysis("model-two", "语言模型架构乙", "模型速读乙。")
    analyses = [model_one, chip, model_two]
    assert [item.event_id for item in group_analyses_by_topic(analyses)] == [
        "model-one", "model-two", "chip",
    ]
    now = datetime(2026, 8, 24, 10, tzinfo=timezone.utc)
    context = {
        "title": "测试日报", "report_date": "2026-08-24",
        "market_date": "2026-08-24", "generated_at": "2026-08-24 18:00 CST",
        "is_trading_day": True, "experiment_id": "test", "run_name": "topic-order",
        "analyses": analyses, "intensive_analyses": analyses, "extensive_analyses": [],
        "ai_status": "enabled", "ai_status_label": "AI深研已启用",
        "usage": {"calls": 0, "input_tokens": 0, "output_tokens": 0},
        "prompt_version": "test-v1", "pipeline_errors": [],
        "market_source_status": [], "intelligence_source_status": [],
    }
    html = publish(
        context, analyses, {"run_name": "topic-order"}, tmp_path, now,
    )["html"].read_text(encoding="utf-8")
    quick = html.split('<nav class="report-tabs"', 1)[0]
    intensive = html.split('id="intensive-panel"', 1)[1].split(
        'id="extensive-panel"', 1
    )[0]
    assert quick.index("模型速读甲") < quick.index("模型速读乙") < quick.index("芯片速读")
    assert (
        intensive.index("大模型架构甲")
        < intensive.index("语言模型架构乙")
        < intensive.index("GPU芯片发布")
    )


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
    with pytest.raises(ValueError, match="repo 格式无效"):
        _validate_sources({
            "apis": [{
                "id": "issues", "type": "github_issues", "repo": "invalid",
                "tier": 3,
            }]
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
    extensive_analysis = analysis.model_copy(update={
        "event_id": "event-2",
        "headline": "泛读事件",
        "plain_takeaway": "第二项事件已经完成分析，但因为排序在前十之外，只用一句话缩略展示。",
    })
    context = {
        "title": "测试日报", "report_date": "2026-08-24", "market_date": "2026-08-24",
        "generated_at": "2026-08-24 18:10 CST", "is_trading_day": True,
        "analyses": [analysis, extensive_analysis],
        "intensive_analyses": [analysis],
        "extensive_analyses": [extensive_analysis],
        "ai_status": "disabled", "ai_status_label": "AI未启用",
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
            "use_cases": ["应用开发者在服务中加载预训练模型，完成文本分类。"],
            "language": "Python",
            "origin": "github",
            "origin_label": "GitHub",
            "reason": "今日最热",
            "stars_today": 1200,
            "stars_week": 5000,
            "stars_total": 150000,
            "stars_total_label": "15万",
            "rank": 1,
        }],
    }
    metadata = {
        "run_name": "100000-test",
        "experiment_id": "test",
        "ai": {"status": "disabled"},
    }
    outputs = publish(
        context, [analysis, extensive_analysis], metadata,
        tmp_path, now,
    )
    assert set(outputs) == {"html"}
    html = outputs["html"].read_text(encoding="utf-8")
    assert 'role="tab"' in html
    assert 'id="intensive-panel"' in html
    assert 'id="extensive-panel"' in html and 'aria-labelledby="extensive-tab" hidden' in html
    assert '<details class="deep-dive">' in html
    assert 'href="https://example.com/source"' in html
    assert "阅读原文" in html
    assert "今日速读" in html
    assert "精读" in html and "泛读" in html
    assert "市场情报" not in html
    assert 'data-tab="intensive">精读</button>' in html
    assert 'data-tab="extensive"' in html
    assert "data-subtab" not in html
    assert "新闻精选" not in html
    digest_html = html.split('id="intensive-panel"', 1)[0]
    assert "实验室公布了一项可核对的工程改进，短时间内还不会大规模落地。" in digest_html
    digest_body = digest_html.split("<h2>今日速读</h2>", 1)[1]
    assert "精读摘要" in digest_body
    assert "scan-kicker" in digest_body
    assert "技术深研" in html
    assert "赚钱效应" not in html
    intensive_html = html.split('id="intensive-panel"', 1)[1].split(
        'id="extensive-panel"', 1
    )[0]
    extensive_html = html.split('id="extensive-panel"', 1)[1].split(
        'id="git-panel"', 1
    )[0]
    assert "产业风向" not in html and "全球市场" not in html
    assert "金融行业" not in extensive_html
    assert "纳斯达克" not in extensive_html and "黄金" not in extensive_html
    assert "scan-cluster" in extensive_html
    assert "影响与后果" not in extensive_html and "依据" not in extensive_html
    assert "可归因事件" not in html
    assert "可能影响" not in extensive_html and "推理过程" not in extensive_html
    assert "深</span>" not in digest_html and ">线索<" not in digest_html
    assert "出口管制" not in html
    assert "第二项事件已经完成分析" in extensive_html
    assert 'class="scan-clusters"' in extensive_html
    assert 'class="scan-cluster"' in extensive_html
    assert "columns:2" in html and "break-inside:avoid" in html
    assert '<details class="deep-dive">' in intensive_html
    assert '<details class="deep-dive">' not in extensive_html
    assert "个股扫描" not in html
    assert "长江证券" not in html
    assert 'id="git-panel"' in html
    assert "huggingface/transformers" in html
    git_html = html.split('id="git-panel"', 1)[1]
    assert 'class="story"' in git_html
    assert "语言与热度" not in git_html
    assert "具体使用场景" in git_html
    assert "应用开发者在服务中加载预训练模型" in git_html
    assert "打开仓库" in git_html and "trend-fill" not in git_html
    assert "今日 +1200" in git_html and "本周 +5000" in git_html
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
        radar_news=pd.DataFrame([{
            "title": "AkShare 雷达事件", "summary": "市场线索",
            "published_at": now.isoformat(), "url": "https://example.com/radar",
        }]),
        context={
            "market_date": "2026-08-25",
            "is_trading_day": True,
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
        radar_news = None

        def run(self, *args, **kwargs):
            self.radar_news = args[1]
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
    intelligence_workflow = IntelligenceWorkflow()
    outputs = run_application(
        settings,
        now=now,
        repository=SQLiteIntelligenceRepository(tmp_path / "intelligence.db"),
        market_workflow=MarketWorkflow(),
        intelligence_workflow=intelligence_workflow,
        github_workflow=GitWorkflow(),
        publisher=publisher,
    )
    assert outputs == {"custom": tmp_path / "custom-output"}
    assert publisher.metadata["ai"]["provider"] == "qwen-code-agent"
    assert publisher.metadata["ai"]["usage_reporting"] == "estimated"
    assert publisher.metadata["intelligence"]["quality"]["policy_version"] == "evidence-gate-v1"
    assert intelligence_workflow.radar_news.iloc[0]["title"] == "AkShare 雷达事件"
    assert publisher.metadata["intelligence"]["intensive_reading_events"] == 0
    assert publisher.metadata["intelligence"]["extensive_reading_events"] == 0


def test_orchestrator_briefs_each_github_project_during_cached_preview(tmp_path) -> None:
    root = Path(__file__).resolve().parents[1]
    settings = load_settings(root / "config" / "settings.yaml")
    now = datetime(2026, 8, 25, 10, tzinfo=timezone.utc)
    market_result = MarketRunResult(
        radar_news=pd.DataFrame(),
        context={
            "market_date": "2026-08-25", "is_trading_day": True,
            "market_source_status": [],
        },
        metadata={"market_date": "2026-08-25"},
    )
    intelligence_result = IntelligenceRunResult(
        analyses=[], source_status=[], ai_status="cached",
        usage={"calls": 0, "input_tokens": 0, "output_tokens": 0, "estimated": False},
        model_runtime={"provider": "fixture", "models": {}},
    )

    class Stages:
        available = True

        def __init__(self) -> None:
            self.names: list[str] = []

        @property
        def usage(self):
            return {
                "calls": len(self.names), "input_tokens": len(self.names) * 10,
                "output_tokens": len(self.names) * 5, "estimated": False,
            }

        def runtime_metadata(self):
            return {
                "provider": "fixture", "configured_models": {"git_brief": "fixture-git"},
                "models": {"git_brief": "fixture-git"}, "usage_reporting": "reported",
            }

        def brief_github(self, projects):
            assert len(projects) == 1
            name = projects[0]["full_name"]
            self.names.append(name)
            use_cases = [] if name == "acme/two" and self.names.count(name) == 1 else [
                f"开发者在具体任务中运行 {name} 并得到结果。"
            ]
            return GitBriefingBatch(items=[GitProjectBrief(
                full_name=name, kicker="工具",
                function=f"{name} 提供一个可核对的工具功能。",
                use_cases=use_cases,
            )])

    class MarketWorkflow:
        def run(self):
            return market_result

    class IntelligenceWorkflow:
        def __init__(self) -> None:
            self.stages = Stages()

        def run(self, *args, **kwargs):
            return intelligence_result

    class GitWorkflow:
        def run(self, *args, **kwargs):
            from daily_intel.github.pipeline import GitRunResult
            return GitRunResult(projects=[
                {"full_name": "acme/one", "description": "one"},
                {"full_name": "acme/two", "description": "two"},
            ])

    class CapturingPublisher:
        context = None
        metadata = None

        def publish(self, context, analyses, metadata, output_dir, now):
            self.context = context
            self.metadata = metadata
            return {"html": tmp_path / "preview.html"}

    intelligence_workflow = IntelligenceWorkflow()
    publisher = CapturingPublisher()
    run_application(
        settings, offline=True, now=now,
        repository=SQLiteIntelligenceRepository(tmp_path / "intelligence.db"),
        market_workflow=MarketWorkflow(), intelligence_workflow=intelligence_workflow,
        github_workflow=GitWorkflow(), publisher=publisher,
    )
    assert intelligence_workflow.stages.names == ["acme/one", "acme/two", "acme/two"]
    assert publisher.context["github_brief_status"] == "generated"
    assert publisher.context["github_briefed_projects"] == 2
    assert publisher.metadata["github"]["brief_status"] == "generated"
    assert publisher.metadata["ai"]["models"]["git_brief"] == "fixture-git"
    assert publisher.metadata["ai"]["usage"]["calls"] == 3


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
