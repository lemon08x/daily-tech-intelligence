from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from jinja2 import Environment, PackageLoader, select_autoescape

from daily_intel.core.models import Analysis, Digest
from daily_intel.market.normalize import clean_text


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


def _render_markdown(context: dict[str, Any], analyses: list[Analysis]) -> str:
    breadth = context["breadth"]
    lines = [
        f"# {context['title']} · {context['report_date']}", "",
        f"> 行情交易日：**{context['market_date']}**；AI状态：**{context['ai_status_label']}**。", "",
        "## 科技前沿深研", "",
    ]
    if not analyses:
        lines.extend(["今日没有可发布的科技事件。请查看数据源状态或在联网后重试。", ""])
    for index, analysis in enumerate(analyses, 1):
        label = "深度结论" if analysis.status.value == "deep" else "线索"
        lines.extend([f"### {index}. {analysis.headline}", "", f"**状态：{label} · 置信度 {analysis.confidence:.0%}**", ""])
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

    lines.extend([
        "## A股市场观察", "",
        f"市场温度：**{breadth['mood']}**；上涨 {breadth['advancing']} 家，下跌 {breadth['declining']} 家，中位涨跌幅 {breadth['median_change']:.2f}%。", "",
        "### 规则候选", "",
        "| 排名 | 代码 | 名称 | 综合分 | 涨跌幅 | 60日 | 入选原因 |",
        "|---:|---|---|---:|---:|---:|---|",
    ])
    for index, row in enumerate(context["candidate_records"], 1):
        lines.append(
            f"| {index} | {row['code']} | {_markdown_text(row['name'])} | {row['score']:.1f} | "
            f"{format_number(row.get('pct_change'))}% | {format_number(row.get('momentum_60d'))}% | {_markdown_text(row['reasons'])} |"
        )
    lines.extend(["", "### 相对强势行业", ""])
    for row in context["hot_industry_records"]:
        lines.append(f"- {row['name']}：{format_number(row.get('pct_change'))}%（领涨：{row.get('leader') or '—'}）")
    lines.extend([
        "", "---",
        "本报告仅做公开信息整理与研究观察。科技事件与公司关联不进入规则股票评分，不构成投资建议。",
    ])
    return "\n".join(lines) + "\n"


def publish(
    context: dict[str, Any], analyses: list[Analysis], snapshot: pd.DataFrame,
    candidates: pd.DataFrame, metadata: dict[str, Any], output_dir: Path, now: datetime,
) -> dict[str, Path]:
    day_dir = output_dir / now.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    env = Environment(
        loader=PackageLoader("daily_intel", "publication/templates"),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters.update(money=format_money, num=format_number, text=clean_text)
    html_path = day_dir / "daily_digest.html"
    markdown_path = day_dir / "daily_digest.md"
    candidate_path = day_dir / "candidates.csv"
    snapshot_path = day_dir / "market_snapshot.csv"
    intelligence_path = day_dir / "intelligence.json"
    metadata_path = day_dir / "run_meta.json"

    html_path.write_text(env.get_template("report.html.j2").render(**context), encoding="utf-8")
    markdown_path.write_text(_render_markdown(context, analyses), encoding="utf-8")
    candidates.to_csv(candidate_path, index=False, encoding="utf-8-sig")
    snapshot.to_csv(snapshot_path, index=False, encoding="utf-8-sig")
    digest = Digest(
        generated_at=now, market_date=context["market_date"], analyses=analyses,
        metadata={"ai_status": context["ai_status"], "report_date": context["report_date"]},
    )
    intelligence_path.write_text(digest.model_dump_json(indent=2), encoding="utf-8")
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "html": html_path, "markdown": markdown_path, "csv": candidate_path,
        "snapshot": snapshot_path, "intelligence": intelligence_path, "metadata": metadata_path,
    }
