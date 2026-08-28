from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from jinja2 import Environment, PackageLoader, select_autoescape

from daily_intel.core.models import Analysis, Digest
from daily_intel.core.runs import sanitize_run_identifier
from daily_intel.market.normalize import clean_text
from daily_intel.publication.plain_digest import build_plain_digest


QUALITY_ISSUE_LABELS = {
    "verifier_not_pass": "校验器未通过",
    "unsupported_claims": "存在未支持结论",
    "insufficient_evidence": "可定位证据不足",
    "missing_primary_source": "缺少一手来源",
    "insufficient_key_facts": "有效事实不足",
    "missing_plain_takeaway": "缺少大白话要点",
    "missing_required_sections": "必填分析段缺失",
    "insufficient_risks": "风险项不足",
    "insufficient_counterpoints": "反面观点不足",
    "ai_not_enabled": "AI未启用",
}


def quality_issue_label(value: str) -> str:
    return QUALITY_ISSUE_LABELS.get(value, value)


def format_money(value: Any) -> str:
    if value is None or pd.isna(value):
        return "—"
    number = float(value)
    if abs(number) >= 1_0000_0000_0000:
        return f"{number / 1_0000_0000_0000:.2f}万亿"
    if abs(number) >= 1_0000_0000:
        return f"{number / 1_0000_0000:.1f}亿"
    if abs(number) >= 1_0000:
        return f"{number / 1_0000:.1f}万"
    return f"{number:.0f}"


def format_number(value: Any, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):,.{digits}f}"


def _markdown_text(value: str) -> str:
    return clean_text(value, 1000).replace("|", "\\|").replace("\n", " ")


def _render_plain_digest_markdown(digest: dict[str, Any]) -> list[str]:
    if not digest.get("has_content"):
        return []
    lines = ["## 今日速读", ""]
    tech_items = digest.get("tech_items") or []
    if tech_items:
        lines.append("**科技**")
        for item in tech_items:
            scan = _markdown_text(item.get("scan") or item.get("headline") or "")
            url = item.get("url") or ""
            if url:
                scan = f"[{scan}]({url})"
            kicker = _markdown_text(item.get("kicker") or "")
            prefix = f"**{kicker}** " if kicker else ""
            lines.append(f"- {prefix}{scan}")
        lines.append("")
    return lines


def _render_markdown(context: dict[str, Any], analyses: list[Analysis]) -> str:
    digest = context.get("plain_digest") or {}
    lines = [
        f"# {context['title']} · {context['report_date']}", "",
        f"> 行情交易日：**{context['market_date']}**；AI状态：**{context['ai_status_label']}**；"
        f"实验：**{context.get('experiment_id', 'default')}**；"
        f"运行：**{context.get('run_name', 'default')}**。", "",
    ]
    lines.extend(_render_plain_digest_markdown(digest))
    lines.extend(["## 新闻精选", ""])
    if not analyses:
        lines.extend(["今日没有可发布的科技事件。请查看数据源状态或在联网后重试。", ""])
    for index, analysis in enumerate(analyses, 1):
        label = "深度结论" if analysis.status.value == "deep" else "线索"
        quality = analysis.quality
        audit = (
            f"质量分 {quality.score}/100 · 有效证据 {quality.supported_evidence} · "
            f"来源 {quality.source_diversity}（一手 {quality.primary_sources}）"
        )
        headline = _markdown_text(analysis.headline)
        if analysis.evidence:
            headline = f"[{headline}]({analysis.evidence[0].url})"
        lines.extend([
            f"### {index}. {headline}", "",
            f"**状态：{label} · 置信度 {analysis.confidence:.0%} · {audit}**", "",
        ])
        if analysis.plain_takeaway:
            lines.extend([f"**一句话：** {_markdown_text(analysis.plain_takeaway)}", ""])
        if quality.issues:
            reasons = "、".join(quality_issue_label(item) for item in quality.issues)
            lines.extend([f"> 质量门降级原因：{reasons}", ""])
        if quality.unsupported_claims:
            lines.append("> 未被来源支持的结论：" + "；".join(
                _markdown_text(item) for item in quality.unsupported_claims
            ))
            lines.append("")
        for fact in analysis.key_facts:
            lines.append(f"- {_markdown_text(fact)}")
        if analysis.technical_mechanism:
            lines.extend(["", f"**技术机制：** {_markdown_text(analysis.technical_mechanism)}"])
        if analysis.novelty:
            lines.extend(["", f"**新颖性：** {_markdown_text(analysis.novelty)}"])
        if analysis.maturity:
            lines.extend(["", f"**成熟度：** {_markdown_text(analysis.maturity)}"])
        if analysis.outlook_6_24m:
            lines.extend(["", f"**6–24个月影响：** {_markdown_text(analysis.outlook_6_24m)}"])
        if analysis.industry_impacts:
            lines.extend(["", "**产业链影响：**", ""])
            for item in analysis.industry_impacts:
                lines.append(f"- {item.segment}（{item.horizon} / {item.direction}）：{_markdown_text(item.rationale)}")
        if analysis.company_mappings:
            lines.extend(["", "**A股关联假设（不参与股票评分）：**", ""])
            for item in analysis.company_mappings:
                state = "已核验关联" if item.status.value == "verified" else "待核验假设"
                industry = f" · 巨潮行业 {item.industry}" if item.industry else ""
                lines.append(f"- {item.code} {item.name}{industry} · {state}：{_markdown_text(item.rationale)}")
                for evidence in item.evidence:
                    lines.append(f"  - [{_markdown_text(evidence.locator)}]({evidence.url})：{_markdown_text(evidence.quote)}")
        if analysis.risks or analysis.counterpoints:
            lines.extend(["", "**风险与反面证据：**", ""])
            lines.extend(f"- {_markdown_text(item)}" for item in analysis.risks + analysis.counterpoints)
        if analysis.evidence:
            lines.extend(["", "**证据：**", ""])
            for evidence in analysis.evidence:
                lines.append(f"- [{_markdown_text(evidence.locator)}]({evidence.url})：{_markdown_text(evidence.quote)}")
        lines.append("")

    projects = context.get("github_projects") or []
    if projects:
        chart = context.get("github_chart") or {}
        lines.extend(["## Git 热门项目", "", "今日最热或本周增长最快的开源项目，用一句话说清它在做什么，并附使用场景模拟。", ""])
        if chart:
            lines.append(
                f"共 {chart.get('count', len(projects))} 个仓库；语言 {chart.get('language_count', 0)} 种；"
                f"今日最高 +{chart.get('max_stars_today', 0)}；本周最高 +{chart.get('max_stars_week', 0)}。"
            )
            lines.append("")
        languages = chart.get("language_bars") or []
        if languages:
            lines.extend(["### 语言分布", ""])
            for item in languages:
                lines.append(
                    f"- {item.get('name', '')} {item.get('label', '')} `{item.get('spark', '')}`"
                )
            lines.append("")
        for item in projects:
            name = _markdown_text(item.get("full_name") or "")
            url = item.get("url") or ""
            title = f"[{name}]({url})" if url else name
            kicker = _markdown_text(item.get("kicker") or "开源")
            reason = _markdown_text(item.get("reason") or "")
            lines.extend([f"### {item.get('rank', '')}. **{kicker}** {title}", ""])
            if item.get("plain"):
                lines.extend([f"**一句话：** {_markdown_text(item['plain'])}", ""])
            meta = []
            if item.get("language"):
                meta.append(str(item["language"]))
            if reason:
                meta.append(reason)
            if item.get("stars_today"):
                meta.append(f"今日 +{item['stars_today']} `{item.get('today_spark', '')}`")
            if item.get("stars_week"):
                meta.append(f"本周 +{item['stars_week']} `{item.get('week_spark', '')}`")
            if meta:
                lines.append("- " + " · ".join(meta))
                lines.append("")
            scene = _markdown_text(item.get("scenario") or "")
            if scene:
                heading = _markdown_text(item.get("scenario_title") or "使用场景模拟")
                lines.extend([f"**{heading}：** {scene}", ""])

    lines.extend([
        "## 市场情报", "",
        "只保留能解释交易原因的事件：政策、监管、供给冲击、合作与禁令。产业和全球市场列出当日涨跌前三后三，或幅度够大的条目。", "",
    ])
    bars = digest.get("industry_bars") or []
    if bars:
        lines.extend(["### 产业风向", ""])
        for item in bars:
            extra = f"：{item['why']}" if item.get("why") else ""
            lines.append(f"- {item.get('name', '')} {item.get('label', '')}{extra}")
        lines.append("")
    board = digest.get("board") or []
    if board:
        lines.extend(["### 全球市场", ""])
        for item in board:
            extra = f"：{item['why']}" if item.get("why") else ""
            lines.append(
                f"- {item.get('region', '')} {item.get('label', '')}：{item.get('label_change', '')}{extra}"
            )
        lines.append("")
    if context.get("news_records"):
        lines.extend(["### 可归因事件", ""])
        for item in context["news_records"]:
            title = _markdown_text(item.get("title", ""))
            if item.get("url"):
                title = f"[{title}]({item['url']})"
            summary = _markdown_text(item.get("summary", ""))
            lines.append(f"- {title}" + (f"：{summary}" if summary else ""))
        lines.append("")
    runtime = context.get("model_runtime", {})
    usage = context.get("usage", {})
    models = runtime.get("models", {})
    if not models and runtime.get("analysis_models"):
        models = {"cached_analysis": runtime["analysis_models"]}
    if not models:
        models = runtime.get("configured_models", {})
    model_text = "、".join(f"{stage}={model}" for stage, model in models.items()) or "无"
    lines.extend([
        "", "## 方法、模型与数据状态", "",
        f"- 实际模型提供方：{runtime.get('provider', 'unknown')}；模型：{model_text}",
        f"- 本次模型调用：{usage.get('calls', 0)} 次；输入 {usage.get('input_tokens', 0)} tokens；"
        f"输出 {usage.get('output_tokens', 0)} tokens"
        f"{'（估算）' if usage.get('estimated') else ''}",
        f"质量策略：{context.get('quality_summary', {}).get('policy_version') or '未产生分析'}；"
        f"平均质量分 {context.get('quality_summary', {}).get('average_score', 0)}；"
        f"通过 {context.get('quality_summary', {}).get('passed', 0)} 项，"
        f"降级 {context.get('quality_summary', {}).get('downgraded', 0)} 项。",
    ])
    for item in context.get("market_source_status", []):
        state = "缓存/降级" if item.get("stale") else "实时成功"
        lines.append(f"- 市场源 {item.get('name', 'unknown')}：{state}")
    for item in context.get("intelligence_source_status", []):
        state = "失败/缓存" if item.get("stale") else "采集成功"
        lines.append(
            f"- 科技源 {item.get('name', 'unknown')}：{state}，{item.get('count', 0)} 条"
        )
    if context.get("pipeline_errors"):
        lines.extend(["", "**独立降级记录：**", ""])
        lines.extend(f"- {_markdown_text(item)}" for item in context["pipeline_errors"])
    lines.extend([
        "", "---", "",
        "本报告仅做公开信息整理与研究观察。科技事件与公司关联不进入规则股票评分，不构成投资建议。",
    ])
    return "\n".join(lines) + "\n"


def publish(
    context: dict[str, Any], analyses: list[Analysis], snapshot: pd.DataFrame,
    candidates: pd.DataFrame, metadata: dict[str, Any], output_dir: Path, now: datetime,
) -> dict[str, Path]:
    day_dir = output_dir / now.strftime("%Y-%m-%d")
    run_name = sanitize_run_identifier(
        str(metadata.get("run_name") or f"{now:%H%M%S}-default")
    )
    run_dir = day_dir / "runs" / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    env = Environment(
        loader=PackageLoader("daily_intel", "publication/templates"),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters.update(money=format_money, num=format_number, text=clean_text)
    env.filters["quality_issue"] = quality_issue_label
    html_path = run_dir / "daily_digest.html"
    markdown_path = run_dir / "daily_digest.md"
    candidate_path = run_dir / "candidates.csv"
    snapshot_path = run_dir / "market_snapshot.csv"
    intelligence_path = run_dir / "intelligence.json"
    metadata_path = run_dir / "run_meta.json"
    run_files = (
        html_path, markdown_path, candidate_path, snapshot_path,
        intelligence_path, metadata_path,
    )
    if any(path.exists() for path in run_files):
        raise FileExistsError(f"运行目录已包含日报文件，拒绝覆盖: {run_dir}")

    render_context = {
        "model_runtime": {},
        "quality_summary": {},
        "github_projects": [],
        "github_chart": {},
        **context,
        "plain_digest": build_plain_digest(analyses, context),
    }
    context = {**context, "plain_digest": render_context["plain_digest"]}
    html_path.write_text(
        env.get_template("report.html.j2").render(**render_context), encoding="utf-8"
    )
    markdown_path.write_text(_render_markdown(context, analyses), encoding="utf-8")
    candidates.to_csv(candidate_path, index=False, encoding="utf-8-sig")
    snapshot.to_csv(snapshot_path, index=False, encoding="utf-8-sig")
    digest = Digest(
        generated_at=now, market_date=context["market_date"], analyses=analyses,
        metadata={
            "ai_status": context["ai_status"],
            "report_date": context["report_date"],
            "experiment_id": context.get("experiment_id", "default"),
            "run_name": context.get("run_name", run_name),
            "quality": context.get("quality_summary", {}),
        },
    )
    intelligence_path.write_text(digest.model_dump_json(indent=2), encoding="utf-8")
    metadata_payload = {
        **metadata,
        "output": {
            "run_name": run_name,
            "run_directory": run_dir.relative_to(output_dir).as_posix(),
        },
    }
    metadata_path.write_text(
        json.dumps(metadata_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    outputs = {
        "html": html_path, "markdown": markdown_path, "csv": candidate_path,
        "snapshot": snapshot_path, "intelligence": intelligence_path, "metadata": metadata_path,
    }
    return outputs
