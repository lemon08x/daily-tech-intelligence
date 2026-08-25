from __future__ import annotations

import json

from daily_intel.core.models import AnalysisDraft, Document, Event


SCOUT_SYSTEM = """你是科技产业情报编辑。只依据用户提供的事件摘要评分，不使用未提供的事实。
识别真正具有技术新颖性、工程深度或未来6至24个月产业影响的事件。输出严格JSON，字段必须符合给定结构。
相关性、创新性、技术深度和产业影响均使用0到100分。营销软文和常规版本更新应低分。"""


ANALYST_SYSTEM = """你是审慎的科技产业研究员。只可使用输入文档中的事实，不得用记忆补全。
写出技术机制、新颖性、成熟度、未来6至24个月影响、风险及反面观点。
evidence.quote必须逐字复制输入文档中的连续文本，并填写对应document_id和URL。
公司关联只是待核验假设，每个事件最多3个；不能确定六位A股代码和名称时不要输出。
输出严格JSON，不要Markdown。"""


VERIFIER_SYSTEM = """你是独立证据审计员。检查草稿是否被给定文档支持。
supported_evidence_indexes只能列出引用确实存在且能支持相关结论的零基索引。
发现过度推断、营销表述当事实或公司映射缺乏依据时写入unsupported_claims。
证据不足时必须downgrade或reject。输出严格JSON。"""


def scout_user(events: list[tuple[Event, list[Document]]], topics: list[dict]) -> str:
    payload = []
    for event, documents in events:
        payload.append(
            {
                "event_id": event.id, "title": event.title,
                "topic_hint": event.topic_id, "deterministic_score": event.deterministic_score,
                "sources": [
                    {"name": doc.source_name, "tier": doc.source_tier, "title": doc.title, "summary": doc.summary[:1500]}
                    for doc in documents
                ],
            }
        )
    return json.dumps(
        {"allowed_topics": [{"id": t["id"], "name": t["name"]} for t in topics], "events": payload,
         "output": {"items": "ScoutItem[]，每个输入event_id恰好一项"}},
        ensure_ascii=False,
    )


def analyst_user(event: Event, documents: list[Document]) -> str:
    payload = {
        "event": event.model_dump(mode="json"),
        "documents": [
            {
                "document_id": doc.id, "source": doc.source_name, "source_tier": doc.source_tier,
                "url": doc.url, "title": doc.title, "published_at": doc.published_at.isoformat(),
                "content": (doc.content or doc.summary)[:50000],
            }
            for doc in documents
        ],
        "requirements": {
            "language": "简体中文", "evidence_minimum": 2,
            "company_hypotheses_maximum": 3, "schema": AnalysisDraft.model_json_schema(),
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def verifier_user(event: Event, documents: list[Document], draft: AnalysisDraft) -> str:
    return json.dumps(
        {
            "event": event.model_dump(mode="json"),
            "documents": [
                {"document_id": doc.id, "url": doc.url, "content": (doc.content or doc.summary)[:50000]}
                for doc in documents
            ],
            "draft": draft.model_dump(mode="json"),
        },
        ensure_ascii=False,
    )
