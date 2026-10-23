from __future__ import annotations

import re
from typing import Any

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from daily_intel.core.models import Analysis
from daily_intel.market.normalize import clean_text
from daily_intel.publication.verification import verification_status

_SHANGHAI = ZoneInfo("Asia/Shanghai")
TOPIC_ID_KICKERS = {
    "foundation_models": "模型",
    "compute_chips": "芯片",
    "robotics": "机器人",
    "cloud_devtools": "软件",
    "cybersecurity": "安全",
    "smart_mobility": "汽车",
    "energy_tech": "能源",
    "biotech": "生物",
}


TOPIC_KICKERS = (
    ("政策", ("政策", "监管", "制裁", "关税", "管制", "禁令", "出口管制")),
    ("机器人", ("robot", "robotics", "embodied", "humanoid", "具身", "机械臂", "机器人", "vla")),
    ("生物", ("protein", "phage", "molecule", "molecular", "smiles", "crispr", "alphafold", "基因组", "蛋白质", "药物", "生物", "分子")),
    ("航天", ("nasa", "rocket", "spacecraft", "mars", "火箭", "航天", "火星", "核热")),
    ("能源", ("battery", "nuclear", "solar", "fusion", "储能", "光伏", "氢能", "核电", "电网")),
    ("安全", ("security", "cyber", "vulnerability", "malware", "网络安全", "信息安全", "漏洞", "入侵", "设备指纹")),
    ("汽车", ("autonomous", "self-driving", "lidar", "adas", "自动驾驶", "智能汽车")),
    ("模型", ("language model", "llm", "moe", "attention", "agent", "qwen", "大模型", "语言模型", "智能体", "注意力", "ai代理")),
    ("软件", ("compiler", "framework", "transformers", "pytorch", "github", "runtime", "开源库")),
    ("芯片", ("gpu", "hbm", "semiconductor", "cuda", "nvfp4", "芯片", "算力", "光刻", "晶圆")),
)

BACKGROUND_HINTS = ("背景", "长期以来", "近年来", "过去几年", "随着", "in recent years")


def _takeaway(analysis: Analysis) -> str:
    if analysis.plain_takeaway.strip():
        return analysis.plain_takeaway.strip()
    for fact in analysis.key_facts:
        if fact.strip():
            return fact.strip()
    if analysis.headline.strip() and not analysis.publication.unverified_placeholder:
        return analysis.headline.strip()
    excerpts = analysis.publication.source_excerpts
    if excerpts:
        return excerpts[0]
    return display_headline(analysis).strip()


def _sentences(value: str) -> list[str]:
    text = clean_text(value, 800).strip()
    parts: list[str] = []
    remaining = text
    while remaining:
        boundary = -1
        chosen = ""
        for mark in ("。", "！", "？", ".", "!", "?"):
            index = remaining.find(mark)
            if index >= 8 and (boundary < 0 or index < boundary):
                boundary = index
                chosen = mark
        if boundary < 0:
            parts.append(remaining)
            break
        parts.append(remaining[: boundary + len(chosen)].strip())
        remaining = remaining[boundary + len(chosen):].strip()
    return [item for item in parts if item]


def _looks_like_background(sentence: str) -> bool:
    compact = sentence.strip()
    if any(hint in compact for hint in BACKGROUND_HINTS) and not any(
        token in compact for token in ("发布", "提出", "达到", "推出", "证明", "实现")
    ):
        return True
    return False


def _scan_line(analysis: Analysis) -> str:
    if (analysis.publication.unverified_placeholder
            and not analysis.scan_sentence.strip() and not analysis.plain_takeaway.strip()):
        return "这条材料暂缺可靠的中文解读，可在详情中查看原文。"
    if analysis.scan_sentence.strip():
        return clean_text(analysis.scan_sentence, 220)
    sentences = _sentences(_takeaway(analysis))
    for sentence in sentences:
        if not _looks_like_background(sentence):
            return sentence
    if sentences:
        return sentences[-1]
    return clean_text(analysis.headline, 80)


def match_kicker(text: str) -> str:
    haystack = text.lower()
    for label, keywords in TOPIC_KICKERS:
        if any(keyword.lower() in haystack for keyword in keywords):
            return label
    return ""


def _shanghai(value: datetime | None) -> str:
    if value is None:
        return "日期未知"
    current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return current.astimezone(_SHANGHAI).strftime("%Y-%m-%d %H:%M")


def _topic_kicker(analysis: Analysis) -> str:
    structured = TOPIC_ID_KICKERS.get(analysis.topic_id, "")
    if not structured and analysis.topic_name:
        for key, label in (
            ("安全", "安全"), ("模型", "模型"), ("芯片", "芯片"), ("机器人", "机器人"),
            ("汽车", "汽车"), ("能源", "能源"), ("生物", "生物"), ("软件", "软件"),
            ("航天", "航天"),
        ):
            if key in analysis.topic_name:
                structured = label
                break
    if structured:
        return structured
    primary = match_kicker(f"{analysis.headline} {_scan_line(analysis)}")
    if primary:
        return primary
    return match_kicker(" ".join(analysis.key_facts)) or "科技"


def group_analyses_by_topic(analyses: list[Analysis]) -> list[Analysis]:
    """Keep first-seen topic order and original Scout order inside each topic."""
    groups: dict[str, list[Analysis]] = {}
    for analysis in analyses:
        groups.setdefault(_topic_kicker(analysis), []).append(analysis)
    return [analysis for group in groups.values() for analysis in group]


def _date_line(analysis: Analysis) -> str:
    meta = analysis.publication
    original = _shanghai(meta.original_published_at)
    source = _shanghai(meta.source_published_at)
    first_seen = _shanghai(meta.first_seen)
    parts = []
    if meta.original_published_at:
        parts.append(f"原始发表 {original}")
    if meta.source_published_at:
        parts.append(f"来源日期 {source}")
    if meta.source_updated_at:
        parts.append(f"页面更新 {_shanghai(meta.source_updated_at)}")
    if not meta.source_published_at and not meta.original_published_at:
        parts.append("原始发布日期未知")
    if meta.first_published_at:
        parts.append(f"首次刊登 {_shanghai(meta.first_published_at)}")
    else:
        parts.append("首次刊登：无历史记录")
    if meta.freshness_label:
        parts.append(meta.freshness_label)
    return " · ".join(parts) if parts else "日期未知"


def display_headline(analysis: Analysis) -> str:
    if analysis.publication.unverified_placeholder:
        return f"{_topic_kicker(analysis)}线索：暂无可靠解读"
    return analysis.headline


def reading_paragraphs(value: str) -> list[str]:
    """Only add visual breaks; keep wording, numbers and qualifications intact."""
    return [part.strip() for part in re.findall(r".+?(?:[。！？][”’」』]?|$)", value, re.S)
            if part.strip()]


def audit_notes(analysis: Analysis) -> list[dict[str, str]]:
    labels = {"headline": "标题", "scan_sentence": "速读", "plain_takeaway": "摘要",
              "key_facts": "事实", "technical_mechanism": "技术机制", "novelty": "新颖性",
              "maturity": "成熟度", "outlook_6_24m": "展望", "industry_impacts": "产业影响",
              "risks": "风险", "counterpoints": "反面信息"}
    return [{"field": labels.get(c.field_path.split("[")[0], "表述"),
             "text": c.text, "reason": c.notes or "该表述尚未取得足够的原文支持"}
            for c in analysis.quality.claim_audits if c.status.value != "supported"]


def build_plain_digest(
    analyses: list[Analysis], context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Lane scan lists for 今日速读. Market news is assembled separately."""
    tech_items = []
    general_items = []
    hardcore_items = []
    for analysis in analyses:
        url = analysis.evidence[0].url if analysis.evidence else ""
        scan = _scan_line(analysis)
        detail_facts: list[str] = []
        seen_facts = {scan.strip(), analysis.headline.strip()}
        for fact in analysis.key_facts:
            cleaned = fact.strip()
            if cleaned and cleaned not in seen_facts:
                detail_facts.append(cleaned)
                seen_facts.add(cleaned)
        item = {
            "event_id": analysis.event_id,
            "headline": display_headline(analysis),
            "kicker": _topic_kicker(analysis),
            "scan": scan,
            "takeaway": _takeaway(analysis),
            "takeaway_paragraphs": (
                [] if analysis.publication.unverified_placeholder and not analysis.plain_takeaway.strip()
                else reading_paragraphs(analysis.plain_takeaway or scan)
            ),
            "url": url,
            "status": analysis.status.value,
            "verified": analysis.status.value == "deep" and analysis.quality.passed,
            "verification": verification_status(analysis),
            "lane": analysis.lane,
            "key_facts": detail_facts,
            "technical_mechanism": clean_text(analysis.technical_mechanism, 900),
            "novelty": clean_text(analysis.novelty, 900),
            "maturity": analysis.maturity,
            "outlook": clean_text(analysis.outlook_6_24m, 900),
            "risks": [value for value in analysis.risks if value.strip()],
            "counterpoints": [
                value for value in analysis.counterpoints if value.strip()
            ],
            "public_notice": analysis.publication.public_notice,
            "research_status": analysis.publication.research_status,
            "was_research_target": analysis.publication.was_research_target,
            "cache_hit": analysis.publication.cache_hit,
            "date_line": _date_line(analysis),
            "freshness_label": analysis.publication.freshness_label,
            "disputed_claims": analysis.publication.disputed_claims,
            "audit_notes": audit_notes(analysis),
            "source_headline": analysis.publication.source_headline,
            "source_excerpts": analysis.publication.source_excerpts,
            "unverified_placeholder": analysis.publication.unverified_placeholder,
            "selection_order": analysis.publication.selection_order,
            "sources": [
                {
                    "url": evidence.url,
                    "locator": evidence.locator,
                    "quote": evidence.quote,
                }
                for evidence in analysis.evidence
            ],
        }
        tech_items.append(item)
        if analysis.lane == "general":
            general_items.append(item)
        else:
            hardcore_items.append(item)
    return {
        "tech_items": tech_items,
        "general_items": general_items,
        "hardcore_items": hardcore_items,
        "has_content": bool(tech_items),
    }
