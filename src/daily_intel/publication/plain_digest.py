from __future__ import annotations

from daily_intel.core.models import Analysis
from daily_intel.market.normalize import clean_text


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


def _takeaway(analysis: Analysis) -> str:
    if analysis.plain_takeaway.strip():
        return analysis.plain_takeaway.strip()
    for fact in analysis.key_facts:
        if fact.strip():
            return fact.strip()
    return analysis.headline.strip()


def _one_sentence(value: str) -> str:
    text = clean_text(value, 400).strip()
    for mark in ("。", "！", "？"):
        index = text.find(mark)
        if index >= 8:
            return text[: index + 1]
    return text


def _scan_line(analysis: Analysis) -> str:
    return _one_sentence(_takeaway(analysis)) or clean_text(analysis.headline, 80)


def match_kicker(text: str) -> str:
    haystack = text.lower()
    for label, keywords in TOPIC_KICKERS:
        if any(keyword.lower() in haystack for keyword in keywords):
            return label
    return ""


def _topic_kicker(analysis: Analysis) -> str:
    # Match the scan line first. Key facts can mention side topics (e.g. a
    # Transformers release that also ships a protein model) and must not win.
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
            cleaned = clean_text(fact, 500).strip()
            if cleaned and cleaned not in seen_facts:
                detail_facts.append(cleaned)
                seen_facts.add(cleaned)
        item = {
            "event_id": analysis.event_id,
            "headline": analysis.headline,
            "kicker": _topic_kicker(analysis),
            "scan": scan,
            "takeaway": _takeaway(analysis),
            "url": url,
            "status": analysis.status.value,
            "lane": analysis.lane,
            "key_facts": detail_facts,
            "technical_mechanism": clean_text(analysis.technical_mechanism, 900),
            "novelty": clean_text(analysis.novelty, 900),
            "maturity": clean_text(analysis.maturity, 900),
            "outlook": clean_text(analysis.outlook_6_24m, 900),
            "risks": [clean_text(value, 500) for value in analysis.risks if value.strip()],
            "counterpoints": [
                clean_text(value, 500) for value in analysis.counterpoints if value.strip()
            ],
            "sources": [
                {
                    "url": evidence.url,
                    "locator": evidence.locator,
                    "quote": clean_text(evidence.quote, 800),
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
