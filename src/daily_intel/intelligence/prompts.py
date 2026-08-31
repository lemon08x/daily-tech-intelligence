from __future__ import annotations

import json

from daily_intel.core.models import (
    AnalysisDraft, DigestBrief, Document, Event, GitBriefingBatch,
    ScoutBatch, VerificationResult,
)
from daily_intel.intelligence.sources.common import event_lane


SCOUT_SYSTEM = """你是科技产业情报编辑。只依据用户提供的事件摘要评分，不使用未提供的事实。
识别真正具有技术新颖性、工程深度或未来6至24个月产业影响的事件。输出严格JSON，字段必须符合给定结构。
相关性、创新性、技术深度和产业影响均使用0到100分。营销软文和常规版本更新应低分。
lane=general 的周刊、IT 热点和社区精选也要保留名额，不要把初筛名额全部给论文。
生物技术应用如果只是常规实验、没有新的计算方法或产业节点，应明显低于大模型、芯片、机器人和开发工具。"""


ANALYST_SYSTEM = """你是审慎的科技产业研究员。只可使用输入文档中的事实，不得用记忆补全。
读者是关心科技产业的非专业人士：先把事情讲明白，再保留必要术语。

字段要求：
- headline：一句人能看懂的标题，写清谁做了什么。不要写成论文题目、发行说明标题或缩写堆砌。
- plain_takeaway：2到3句大白话。先说发生了什么、为什么值得看；专业名词第一次出现必须立刻用人话解释，例如“稀疏注意力（QSA，只精读最相关的几段，而不是整篇硬读）”。
- key_facts：每条先写结论，再跟必要数字或专名。数字要带单位和对照。禁止把论文摘要、变更列表或内部指标名原样搬进来。
- technical_mechanism、novelty、maturity、outlook_6_24m：给愿意展开的读者看，仍须在首次出现时解释术语。
解释只能转述文档已给出的机制，不得借解释引入文档没有的数字、排名、国别、产品能力或因果判断。
evidence.quote必须逐字复制输入文档中的连续文本，并填写对应document_id和URL。
不要输出公司代码或个股映射。
严格遵守requirements.quality_contract的数量和长度边界；不要为了填满字段重复事实或堆砌引用。
输出严格JSON，不要Markdown。"""


VERIFIER_SYSTEM = """你是独立证据审计员。检查草稿是否被给定文档支持。
supported_evidence_indexes只能列出引用确实存在且能支持相关结论的零基索引。
发现过度推断、营销表述当事实时写入unsupported_claims。
术语解释和类比只要不引入新的数量、排名、国别、产品能力或因果判断，不算新事实；若解释把文档未写的效果说成已验证事实，必须写入unsupported_claims。
只要存在实质性unsupported_claims，verdict就不得为pass；证据不足时必须downgrade或reject。
不要因文字完整、引用数量多或模型自报置信度高而放宽标准。输出严格JSON。"""


DIGEST_BRIEF_SYSTEM = """你是科技与市场日报编辑。只依据用户提供的市场新闻写稿，不使用未提供的事实。
每条只写 kicker（2到4字主题词，如 政策、芯片、能源、汽车）和 scan（一句完整话，说清谁做了什么）。
不要写影响分析、不要写依据段落、不要预测涨跌，不要编造未出现的公司、数字或政策细节。输出严格JSON。"""


GIT_BRIEF_SYSTEM = """你是开源产品编辑。只依据用户提供的仓库名称、简介、README 和源码线索，用一句话说清这个项目的业务功能：给什么人解决什么问题。
优先读 README；README 太短或像徽章堆砌时，再用目录和清单文件（package.json / pyproject.toml 等）判断它是库、工具还是应用。
不要复述星标或热度，不要罗列技术栈，不要编造未给出的用户量、融资或公司采用。
kicker 用 2 到 4 字中文主题词。输出严格JSON。"""


def digest_brief_user(payload: dict) -> str:
    return json.dumps(
        {
            "material": payload,
            "requirements": {
                "language": "简体中文",
                "quotes": "必须是给定 title 或 summary 中的连续原文。",
                "schema": DigestBrief.model_json_schema(),
            },
        },
        ensure_ascii=False,
    )


def _clip_text(value: str, max_chars: int) -> str:
    text = (value or "").strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars]


def scout_user(
    events: list[tuple[Event, list[Document]]], topics: list[dict], *,
    doc_chars: int = 4000,
) -> str:
    payload = []
    for event, documents in events:
        payload.append(
            {
                "event_id": event.id, "title": event.title,
                "topic_hint": event.topic_id, "lane": event_lane(documents),
                "deterministic_score": event.deterministic_score,
                "sources": [
                    {
                        "name": doc.source_name, "tier": doc.source_tier,
                        "title": doc.title,
                        "excerpt": _clip_text(doc.content or doc.summary, doc_chars),
                    }
                    for doc in documents
                ],
            }
        )
    return json.dumps(
        {"allowed_topics": [{"id": t["id"], "name": t["name"]} for t in topics], "events": payload,
         "output": "JSON object matching this schema: "
                   + json.dumps(ScoutBatch.model_json_schema(), ensure_ascii=False)},
        ensure_ascii=False,
    )


def analyst_user(
    event: Event, documents: list[Document], quality_contract: dict | None = None,
    *, max_chars: int = 50000,
) -> str:
    payload = {
        "event": event.model_dump(mode="json"),
        "documents": [
            {
                "document_id": doc.id, "source": doc.source_name, "source_tier": doc.source_tier,
                "url": doc.url, "title": doc.title, "published_at": doc.published_at.isoformat(),
                "content": _clip_text(doc.content or doc.summary, max_chars),
            }
            for doc in documents
        ],
        "requirements": {
            "language": "简体中文", "evidence_minimum": 2,
            "schema": AnalysisDraft.model_json_schema(),
            "quality_contract": quality_contract or {},
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def git_brief_user(projects: list[dict]) -> str:
    payload = [
        {
            "full_name": item.get("full_name") or "",
            "description": item.get("description") or "",
            "language": item.get("language") or "",
            "reason": item.get("reason") or "",
            "readme": _clip_text(str(item.get("readme") or ""), 4500),
            "root_files": _clip_text(str(item.get("root_files") or ""), 400),
            "manifest": _clip_text(str(item.get("manifest") or ""), 1500),
        }
        for item in projects[:12]
    ]
    return json.dumps(
        {
            "projects": payload,
            "requirements": {
                "language": "简体中文",
                "focus": "业务功能：给谁、解决什么问题",
                "prefer": "README，其次清单文件和目录",
                "schema": GitBriefingBatch.model_json_schema(),
            },
        },
        ensure_ascii=False,
    )


def verifier_user(
    event: Event, documents: list[Document], draft: AnalysisDraft, *,
    max_chars: int = 50000,
) -> str:
    return json.dumps(
        {
            "event": event.model_dump(mode="json"),
            "documents": [
                {
                    "document_id": doc.id, "url": doc.url,
                    "content": _clip_text(doc.content or doc.summary, max_chars),
                }
                for doc in documents
            ],
            "draft": draft.model_dump(mode="json"),
            "output": "JSON object matching this schema: "
                      + json.dumps(VerificationResult.model_json_schema(), ensure_ascii=False),
        },
        ensure_ascii=False,
    )
