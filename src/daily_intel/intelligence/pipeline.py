from __future__ import annotations

import json
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from daily_intel.core.models import (
    Analysis, AnalysisDraft, AnalysisStatus, Document, Event, Evidence, ScoutBatch,
    VerificationResult,
)
from daily_intel.core.ports import IntelligenceRepository, LLMClient
from daily_intel.intelligence.clustering import cluster_documents, is_obvious_build_title
from daily_intel.intelligence.extraction import enrich_document
from daily_intel.intelligence.mapping import CompanyMapper
from daily_intel.intelligence.prompts import (
    ANALYST_SYSTEM, SCOUT_SYSTEM, VERIFIER_SYSTEM, analyst_user, scout_user, verifier_user,
)
from daily_intel.intelligence.sources.feeds import build_sources, canonicalize_url, _content_hash, _document_id


@dataclass(slots=True)
class IntelligenceRunResult:
    analyses: list[Analysis]
    source_status: list[dict[str, Any]]
    ai_status: str
    usage: dict[str, int]
    collected_documents: int = 0
    clustered_events: int = 0
    errors: list[str] = field(default_factory=list)


class IntelligencePipeline:
    def __init__(self, settings: dict, repository: IntelligenceRepository, llm: LLMClient) -> None:
        self.settings = settings
        self.config = settings["intelligence"]
        self.repository = repository
        self.llm = llm
        self.prompt_version = settings["llm"]["prompt_version"]

    def run(
        self, now: datetime, snapshot: pd.DataFrame, radar_news: pd.DataFrame,
        offline: bool = False, no_ai: bool = False, require_ai: bool = False,
    ) -> IntelligenceRunResult:
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if require_ai and (offline or no_ai or not self.llm.available):
            raise RuntimeError("--require-ai 已启用，但AI密钥不可用或与离线/禁用AI模式冲突")
        if offline:
            limit = int(self.config["max_deep_events"])
            analyses = [
                item for item in self.repository.get_latest_analyses(limit * 4)
                if not is_obvious_build_title(item.headline)
            ][:limit]
            return IntelligenceRunResult(
                analyses=analyses, source_status=[{"name": "technology_intelligence", "stale": True, "error": "离线模式"}],
                ai_status="cached" if analyses else "unavailable", usage=self.repository.llm_usage_since(day_start),
            )
        documents, source_status = self._collect_sources(now)
        radar_since = self._collection_since(now, "market_radar")
        documents.extend(self._radar_documents(radar_news, now, radar_since))
        for document in documents:
            self.repository.upsert_document(document)
        self.repository.set_state("source_cursor:market_radar", now.isoformat())

        recent_since = now.astimezone(timezone.utc) - timedelta(hours=int(self.config["cluster_window_hours"]))
        recent_documents = self.repository.recent_documents(recent_since)
        events = cluster_documents(
            recent_documents, self.settings["topics"], int(self.config["cluster_window_hours"]),
            int(self.config["title_similarity_threshold"]),
        )[: int(self.config["max_scout_events"])]
        for event in events:
            self.repository.upsert_event(event)

        event_docs = [(event, self.repository.get_documents(event.document_ids)) for event in events]
        ai_enabled = self.llm.available and not no_ai
        if ai_enabled and event_docs:
            selected, scout_error = self._scout(event_docs)
        else:
            selected, scout_error = event_docs, None
        selected = self._balanced_selection(selected)

        analyses: list[Analysis] = []
        errors: list[str] = []
        if scout_error:
            errors.append(scout_error)
        target_count = int(self.config["max_deep_events"])
        for event, docs in selected:
            if len(analyses) >= target_count:
                break
            cached = self.repository.get_analysis(event.id)
            if cached and (cached.model != "none" or not ai_enabled):
                analyses.append(cached)
                continue
            if not ai_enabled:
                analysis = self._lead(event, docs, now, "AI未启用，仅展示权威来源线索")
            else:
                try:
                    enriched = [
                        enrich_document(doc, int(self.config["source_fetch_timeout_seconds"]), int(self.config["full_text_max_chars"]))
                        for doc in docs
                    ]
                    for doc in enriched:
                        self.repository.update_document_content(doc)
                        if doc.metadata.get("extraction_error"):
                            errors.append(
                                f"extraction {doc.source_id}/{doc.id}: {doc.metadata['extraction_error']}"
                            )
                    analysis = self._analyze(event, enriched, snapshot, now)
                except Exception as exc:
                    errors.append(f"{event.id}: {type(exc).__name__}: {exc}")
                    # Failed or invalid model output is never converted into fabricated analysis.
                    # Continue with another ranked event so one failure does not stop the digest.
                    continue
            self.repository.save_analysis(analysis)
            analyses.append(analysis)

        return IntelligenceRunResult(
            analyses=analyses, source_status=source_status,
            ai_status="enabled" if ai_enabled else "disabled",
            usage=self.repository.llm_usage_since(day_start), collected_documents=len(documents),
            clustered_events=len(events), errors=errors,
        )

    def _collection_since(self, now: datetime, source_id: str) -> datetime:
        state = self.repository.get_state(f"source_cursor:{source_id}")
        if not state:
            # One-version compatibility with the early global-cursor prototype.
            state = self.repository.get_state("last_collect_at")
        if not state:
            return now.astimezone(timezone.utc) - timedelta(hours=int(self.config["first_run_lookback_hours"]))
        previous = datetime.fromisoformat(state)
        return previous.astimezone(timezone.utc) - timedelta(hours=int(self.config["resume_overlap_hours"]))

    def _collect_sources(self, now: datetime) -> tuple[list[Document], list[dict[str, Any]]]:
        sources = build_sources(self.settings["sources"], int(self.config["source_fetch_timeout_seconds"]))
        documents: list[Document] = []
        statuses: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(
                    source.collect, self._collection_since(now, source.source_id),
                    int(self.config["max_items_per_source"]),
                ): source
                for source in sources
            }
            for future in as_completed(futures):
                source = futures[future]
                try:
                    items = future.result()
                    documents.extend(items)
                    self.repository.set_state(f"source_cursor:{source.source_id}", now.isoformat())
                    statuses.append({
                        "name": source.source_id, "source": getattr(source, "config", {}).get("name", source.source_id),
                        "fetched_at": now.isoformat(timespec="seconds"), "stale": False,
                        "count": len(items), "error": "",
                    })
                except Exception as exc:
                    statuses.append({
                        "name": source.source_id, "source": getattr(source, "config", {}).get("name", source.source_id),
                        "fetched_at": "", "stale": True, "count": 0,
                        "error": f"{type(exc).__name__}: {exc}"[:300],
                    })
        return documents, sorted(statuses, key=lambda item: item["name"])

    @staticmethod
    def _balanced_selection(
        event_docs: list[tuple[Event, list[Document]]]
    ) -> list[tuple[Event, list[Document]]]:
        """Keep rank order while giving each covered topic one early slot."""
        first_by_topic: list[tuple[Event, list[Document]]] = []
        remainder: list[tuple[Event, list[Document]]] = []
        seen: set[str] = set()
        for item in event_docs:
            topic = item[0].topic_id
            if topic not in seen:
                seen.add(topic)
                first_by_topic.append(item)
            else:
                remainder.append(item)
        return first_by_topic + remainder

    def _radar_documents(self, news: pd.DataFrame, now: datetime, since: datetime) -> list[Document]:
        documents: list[Document] = []
        if news.empty:
            return documents
        for index, row in news.head(30).iterrows():
            published = pd.to_datetime(row.get("published_at"), errors="coerce")
            if pd.isna(published):
                published_at = now
            else:
                timestamp = pd.Timestamp(published)
                if timestamp.tzinfo is None:
                    timestamp = timestamp.tz_localize(now.tzinfo)
                else:
                    timestamp = timestamp.tz_convert(now.tzinfo)
                published_at = timestamp.to_pydatetime()
            if published_at.astimezone(timezone.utc) < since.astimezone(timezone.utc):
                continue
            title, summary = str(row.get("title", "")), str(row.get("summary", ""))
            url = str(row.get("url", "")) or f"radar://{index}"
            external_id = url if not url.startswith("radar://") else f"{published_at.isoformat()}:{title}"
            documents.append(
                Document(
                    id=_document_id("market_radar", external_id), source_id="market_radar",
                    source_name="同花顺/新浪快讯线索", external_id=external_id, title=title,
                    url=url, canonical_url=canonicalize_url(url), published_at=published_at,
                    fetched_at=now, summary=summary, content=summary,
                    content_hash=_content_hash(title, summary), source_tier=3, content_type="news_radar",
                    extraction_quality="summary", metadata={"source_name": "同花顺/新浪快讯线索"},
                )
            )
        return documents

    def _scout(self, event_docs: list[tuple[Event, list[Document]]]) -> tuple[list[tuple[Event, list[Document]]], str | None]:
        signature = hashlib.sha256(
            json.dumps([event.id for event, _ in event_docs], separators=(",", ":")).encode()
        ).hexdigest()
        cache_key = f"scout_selection:{self.prompt_version}"
        cached = self.repository.get_state(cache_key)
        if cached:
            try:
                payload = json.loads(cached)
                if payload.get("signature") == signature:
                    by_id = {event.id: (event, docs) for event, docs in event_docs}
                    return [by_id[event_id] for event_id in payload["event_ids"] if event_id in by_id], None
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                pass
        try:
            result = self._generate_with_retry("scout", SCOUT_SYSTEM, scout_user(event_docs, self.settings["topics"]), ScoutBatch, None)
            by_id = {item.event_id: item for item in result.value.items if item.relevant}
            ranked = []
            for event, docs in event_docs:
                item = by_id.get(event.id)
                if not item:
                    continue
                score = event.source_quality * .25 + item.relevance * .25 + item.novelty * .20 + item.technical_depth * .15 + item.industry_impact * .15
                ranked.append((score, event, docs))
            ranked.sort(key=lambda item: item[0], reverse=True)
            selected = [(event, docs) for _, event, docs in ranked]
            self.repository.set_state(
                cache_key,
                json.dumps({"signature": signature, "event_ids": [event.id for event, _ in selected]}),
            )
            return selected, None
        except Exception as exc:
            return event_docs, f"scout: {type(exc).__name__}: {exc}"

    def _analyze(self, event: Event, docs: list[Document], snapshot: pd.DataFrame, now: datetime) -> Analysis:
        draft_result = self._generate_with_retry("analyst", ANALYST_SYSTEM, analyst_user(event, docs), AnalysisDraft, event.id)
        draft = draft_result.value
        verify_result = self._generate_with_retry(
            "verifier", VERIFIER_SYSTEM, verifier_user(event, docs, draft), VerificationResult, event.id
        )
        verified_evidence = self._verified_evidence(draft, verify_result.value, docs)
        has_primary = any(doc.source_tier == 1 for doc in docs)
        deep = verify_result.value.verdict == "pass" and len(verified_evidence) >= 2 and has_primary
        mapper = CompanyMapper(snapshot, now, offline=False)
        mappings = mapper.resolve(draft.company_hypotheses)
        confidence = max(0.0, min(1.0, draft.confidence + verify_result.value.confidence_adjustment))
        return Analysis(
            event_id=event.id, status=AnalysisStatus.DEEP if deep else AnalysisStatus.LEAD,
            headline=draft.headline, key_facts=draft.key_facts,
            technical_mechanism=draft.technical_mechanism if deep else "",
            novelty=draft.novelty if deep else "", maturity=draft.maturity if deep else "",
            outlook_6_24m=draft.outlook_6_24m if deep else "",
            industry_impacts=draft.industry_impacts if deep else [], risks=draft.risks,
            counterpoints=draft.counterpoints, confidence=confidence if deep else min(confidence, .49),
            evidence=verified_evidence, company_mappings=mappings if deep else [],
            model=draft_result.model, prompt_version=self.prompt_version, created_at=now,
        )

    @staticmethod
    def _verified_evidence(draft: AnalysisDraft, verification: VerificationResult, docs: list[Document]) -> list[Evidence]:
        docs_by_id = {doc.id: doc for doc in docs}
        accepted: list[Evidence] = []
        for index in verification.supported_evidence_indexes:
            if index < 0 or index >= len(draft.evidence):
                continue
            evidence = draft.evidence[index]
            document = docs_by_id.get(evidence.document_id)
            if document is None:
                continue
            if evidence.url.rstrip("/") != document.url.rstrip("/"):
                continue
            haystack = re.sub(r"\s+", "", document.content or document.summary)
            needle = re.sub(r"\s+", "", evidence.quote)
            if needle and needle in haystack:
                accepted.append(evidence)
        return accepted

    def _generate_with_retry(self, stage: str, system: str, user: str, schema: type, event_id: str | None):
        error: Exception | None = None
        for _ in range(2):
            try:
                result = self.llm.generate(stage, system, user, schema)
                self.repository.record_llm_run(
                    stage, event_id, result.model, self.prompt_version,
                    result.input_tokens, result.output_tokens, "success",
                )
                return result
            except Exception as exc:
                error = exc
        model = self.settings["llm"][stage]["model"]
        self.repository.record_llm_run(stage, event_id, model, self.prompt_version, 0, 0, "failed", str(error))
        raise error or RuntimeError("LLM调用失败")

    def _lead(self, event: Event, docs: list[Document], now: datetime, reason: str) -> Analysis:
        evidence = [
            Evidence(document_id=doc.id, url=doc.url, quote=(doc.summary or doc.title)[:800], locator="来源摘要")
            for doc in docs if len(doc.summary or doc.title) >= 8
        ][:3]
        return Analysis(
            event_id=event.id, status=AnalysisStatus.LEAD, headline=event.title,
            key_facts=[reason] + [doc.summary[:300] for doc in docs[:2] if doc.summary],
            risks=["尚未完成强模型深研与独立证据校验"], confidence=.3,
            evidence=evidence, model="none", prompt_version=self.prompt_version, created_at=now,
        )
