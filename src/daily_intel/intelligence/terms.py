from __future__ import annotations

import re

from daily_intel.core.models import ScoutPublicFact

# Context-aware public-field glossary. Never apply these substitutions to
# evidence quotes or other source excerpts.
_TERM_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bresolution rates?\b", re.IGNORECASE), "任务解决率"),
    (re.compile(r"任务解析率"), "任务解决率"),
    (re.compile(r"\btraining-free\b", re.IGNORECASE), "无需训练"),
    (re.compile(r"训练免费"), "无需训练"),
    (re.compile(r"\bpercentage points?\b", re.IGNORECASE), "个百分点"),
    (re.compile(r"\bpp\b"), "个百分点"),
)

TOPIC_KICKERS = {
    "foundation_models": "模型",
    "compute_chips": "芯片",
    "robotics": "机器人",
    "cloud_devtools": "软件",
    "cybersecurity": "安全",
    "smart_mobility": "汽车",
    "energy_tech": "能源",
    "biotech": "生物",
}


def localize_public_text(value: str) -> str:
    text = value or ""
    for pattern, replacement in _TERM_RULES:
        text = pattern.sub(replacement, text)
    return text


def localize_public_facts(facts: list[ScoutPublicFact]) -> list[ScoutPublicFact]:
    localized: list[ScoutPublicFact] = []
    for item in facts:
        localized.append(item.model_copy(update={
            "text": localize_public_text(item.text),
            "quote": item.quote,
        }))
    return localized


def topic_kicker(topic_id: str, topic_name: str = "") -> str:
    if topic_id in TOPIC_KICKERS:
        return TOPIC_KICKERS[topic_id]
    if topic_name:
        for key, label in (
            ("安全", "安全"), ("模型", "模型"), ("芯片", "芯片"), ("机器人", "机器人"),
            ("汽车", "汽车"), ("能源", "能源"), ("生物", "生物"), ("软件", "软件"),
            ("航天", "航天"), ("云", "软件"),
        ):
            if key in topic_name:
                return label
    return ""
