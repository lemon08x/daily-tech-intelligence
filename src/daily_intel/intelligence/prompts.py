from __future__ import annotations

import json

from daily_intel.core.models import (
    AnalysisDraft, ClaimAudit, DigestBrief, Document, Event, GitBriefingBatch,
    ScoutBatch, VerificationResult,
)
from daily_intel.intelligence.sources.common import event_lane
from daily_intel.intelligence.claims import extract_claims


SCOUT_SYSTEM = """你是科技产业情报编辑。只依据用户提供的事件材料评分，不使用未提供的事实。
必须为输入中的每一个 event_id 返回且只返回一个结果，不得因为主题不明确或价值较低而省略。
为每个事件选择一个 allowed_topics 中的 topic_id；无法归类时使用 other。rule_topic_hint 只供参考，你必须独立判断。
识别真正具有技术新颖性、工程深度或未来6至24个月产业影响的事件。输出严格JSON，字段必须符合给定结构。
相关性、创新性、技术深度和产业影响均使用0到100分。营销软文和常规版本更新应低分。
scan 必须是一句可直接发布的简体中文事实摘要，只复述材料明确写出的“谁做了什么/得到什么结果”，不得预测、补背景或写投资判断。
reason 只说明编辑取舍理由，不要把 reason 写成对外摘要，也不要把“创新性一般”等过程评论写进 public_facts。
复排时额外给出 public_headline（中文标题）、public_summary（完整事件摘要）和最多 2 到 3 条 public_facts。每条 public_facts 写对外事实并附 document_id 与材料中的精确连续片段 quote。材料不足时少写，不要把截断英文摘要充当中文事实。
术语：resolution rate 译为任务解决率，training-free 译为无需训练，percentage points 译为个百分点；FlashAttention 保留原名并按材料解释。
lane=general 的周刊、IT 热点和社区精选也应得到公允判断，不要把高分全部给论文。
生物技术应用如果只是常规实验、没有新的计算方法或产业节点，应明显低于大模型、芯片、机器人和开发工具。"""


ANALYST_SYSTEM = """你是审慎的科技产业研究员。只可使用输入文档中的事实，不得用记忆补全。
读者是关心科技产业的非专业人士：先把事情讲明白，再保留必要术语。

字段要求：
- headline：一句人能看懂的标题，写清谁做了什么。不要写成论文题目、发行说明标题或缩写堆砌。
- scan_sentence：独立速读句，说明谁做了什么及主要结果；不要只写背景。
- plain_takeaway：2到3句大白话。先说发生了什么、为什么值得看；专业名词第一次出现必须立刻用人话解释，例如“稀疏注意力（QSA，只精读最相关的几段，而不是整篇硬读）”。
- key_facts：每条先写结论，再跟必要数字或专名。数字要带单位和对照。禁止把论文摘要、变更列表或内部指标名原样搬进来。
- technical_mechanism、novelty、maturity、outlook_6_24m：给愿意展开的读者看，仍须在首次出现时解释术语。展望必须标明不确定性，不得把预测写成已验证事实。
比较结论必须给出对象、指标、方向、数值、单位、基线、任务/测试集和实验条件。百分比变化与百分点变化不得混用。不能仅复述作者“领先/最高/首次”的总结。
解释只能转述文档已给出的机制，不得借解释引入文档没有的数字、排名、国别、产品能力或因果判断。
evidence.quote必须逐字复制输入文档中的连续文本，并填写对应document_id和URL。
不要输出公司代码或个股映射。
严格遵守requirements.quality_contract的数量和长度边界；不要为了填满字段重复事实或堆砌引用。
输出严格JSON，不要Markdown。"""


VERIFIER_SYSTEM = """你是独立证据审计员。检查草稿是否被给定文档支持。只使用用户提供的文档，不得在未见的全文中补找证据。
supported_evidence_indexes只能列出引用确实存在且能支持相关结论的零基索引。
必须为标题、速读句、摘要中的每一句、每条关键事实及其他实质性断言填写 claim_checks，覆盖全部字段路径；一个真实数字不能证明整段结论。
严格逐一审核 claim_inventory，不得自行分句或新增编号；每个 claim_id 原样返回且仅一次。
claim_checks 使用紧凑格式：返回 claim_id、status、document_id、quote、locator、notes；省略 field_path 和 text，由程序按固定清单绑定；如返回这两个字段则必须与清单原样一致。
每条 supported 必须填 document_id 和 quote，quote 必须逐字复制一段连续原文；禁止省略号拼接、改写或补词。无法给出连续支持原文时标为 unverifiable。
状态只能是 supported、unsupported、conflicting、unverifiable。
独立检查相关表格、反例与限制段落，不只审核草稿已挑出的引文。作者叙述与表格冲突时标为 conflicting，并拒绝领先/最高等整体比较结论。
无法确认比较范围、表头或测试条件时标为 unverifiable，不要猜测。
解释性文字若偷偷引入新的效果、排名或因果结论，必须标为 unsupported。
6至24个月展望可以是明确标注的不确定判断，但不能把预测写成已验证事实。
只要存在实质性 unsupported、conflicting 或 unverifiable，verdict 就不得为 pass。
不要因文字完整、引用数量多或模型自报置信度高而放宽标准。输出严格JSON。"""


DIGEST_BRIEF_SYSTEM = """你是科技与市场日报编辑。只依据用户提供的市场新闻写稿，不使用未提供的事实。
每条只写 kicker（2到4字主题词，如 政策、芯片、能源、汽车）和 scan（一句完整话，说清谁做了什么）。
不要写影响分析、不要写依据段落、不要预测涨跌，不要编造未出现的公司、数字或政策细节。输出严格JSON。"""


GIT_BRIEF_SYSTEM = """你是开源产品编辑。只依据用户提供的仓库名称、简介、README、清单文件和源码片段，说明项目给谁、解决什么问题，并给出具体使用场景。
优先读 README，并用清单文件、目录和源码入口交叉核对它是库、工具还是应用。function 用一到两句简体中文讲清核心功能，不要照抄英文简介。
use_cases 给出 2 到 3 个由材料直接支持的具体场景；每条写清“谁在什么任务中如何使用、得到什么结果”，不要写“提高效率”“用于开发”等空泛套话。材料不足时宁可少写，不得凭常识补齐。
不要复述星标或热度，不要罗列技术栈，不要编造未给出的用户量、融资或公司采用。
kicker 用 2 到 4 个中文字符概括项目类型，不要中英混写。输出严格JSON。"""


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
                "rule_topic_hint": event.topic_id, "lane": event_lane(documents),
                "deterministic_score": event.deterministic_score,
                "sources": [
                    {
                        "document_id": doc.id, "url": doc.url,
                        "name": doc.source_name, "tier": doc.source_tier,
                        "title": doc.title,
                        "excerpt": _clip_text(doc.content or doc.summary, doc_chars),
                    }
                    for doc in documents
                ],
            }
        )
    return json.dumps(
        {"allowed_topics": [
             *[{"id": t["id"], "name": t["name"]} for t in topics],
             {"id": "other", "name": "其他科技"},
         ], "events": payload,
         "requirements": "必须逐一返回全部 event_id；主题不确定时 topic_id=other，不得省略。",
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
            "source_excerpt": _clip_text(str(item.get("source_excerpt") or ""), 1800),
        }
        for item in projects[:12]
    ]
    return json.dumps(
        {
            "projects": payload,
            "requirements": {
                "language": "简体中文",
                "focus": "业务功能与具体使用场景：谁在什么任务中如何使用、得到什么结果",
                "prefer": "README 为主，清单文件、目录和源码片段交叉核对",
                "schema": GitBriefingBatch.model_json_schema(),
            },
        },
        ensure_ascii=False,
    )


def verifier_user(
    event: Event, documents: list[Document], draft: AnalysisDraft, *,
    max_chars: int = 50000, claims: list[ClaimAudit] | None = None,
) -> str:
    return json.dumps(
        {
            "event": event.model_dump(mode="json"),
            "documents": [
                {
                    "document_id": doc.id, "url": doc.url, "title": doc.title,
                    "content": _clip_text(doc.content or doc.summary, max_chars),
                }
                for doc in documents
            ],
            "draft": draft.model_dump(mode="json"),
            "claim_inventory": [dict(claim_id=c.claim_id, field_path=c.field_path, text=c.text) for c in (claims if claims is not None else extract_claims(draft))],
            "output": "JSON object matching this schema: "
                      + json.dumps(VerificationResult.model_json_schema(), ensure_ascii=False),
        },
        ensure_ascii=False,
    )
