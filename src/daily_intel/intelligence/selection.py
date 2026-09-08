from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from daily_intel.core.models import (
    Document, Event, PublicationRecord, ScoutBatch, ScoutItem,
)
from daily_intel.intelligence.snapshots import (
    canonical_urls, content_fingerprint, hours_since,
)
from daily_intel.intelligence.terms import localize_public_facts, localize_public_text
from daily_intel.core.progress import progress
from daily_intel.core.ports import IntelligenceRepository
from daily_intel.intelligence.modeling import ModelStageRunner
from daily_intel.intelligence.clustering import UNCLASSIFIED_TOPIC_ID, UNCLASSIFIED_TOPIC_NAME
from daily_intel.intelligence.sources.common import event_lane


class EventSelector:
    """Rank-fuses deterministic signals with bounded model judgement."""

    VERSION = "rank-fusion-v6-quality"

    def __init__(
        self, settings: dict[str, Any], repository: IntelligenceRepository,
        stages: ModelStageRunner,
    ) -> None:
        self.settings = settings
        self.config = settings["intelligence"]
        self.repository = repository
        self.stages = stages
        self.broad_config = dict(settings.get("broad_reading") or {})
        self.broad_reading_enabled = bool(self.broad_config.get("enabled", False))
        self.broad_prompt_version = str(
            self.broad_config.get("prompt_version", "broad-reading-v1")
        )
        self.shortlist_events = max(
            1, int(self.broad_config.get("shortlist_events", 40))
        )
        self.deterministic_weight = float(self.config.get("selection_deterministic_weight", .35))
        self.model_weight = float(self.config.get("selection_model_weight", .65))
        self.model_reject_floor = float(self.config.get("selection_model_reject_floor", 55))
        self.repeat_penalty = float(self.config.get("selection_repeat_penalty", .4))
        self.repeat_hours = float(self.config.get("selection_repeat_hours", 36))
        self.freshness_0_24h = float(self.config.get("selection_freshness_0_24h", 1.0))
        self.freshness_24_48h = float(self.config.get("selection_freshness_24_48h", .9))
        self.freshness_48_72h = float(self.config.get("selection_freshness_48_72h", .75))
        self.experiment_id = "default"
        self.is_production = False
        self.topic_names = {
            str(item["id"]): str(item["name"])
            for item in settings.get("topics", [])
        }
        self.topic_names[UNCLASSIFIED_TOPIC_ID] = UNCLASSIFIED_TOPIC_NAME
        self.last_trace: list[dict[str, Any]] = []
        self.last_funnel: dict[str, int] = {}
        self.last_scan_details: dict[str, dict[str, Any]] = {}
        self.last_exposure: dict[str, dict[str, Any]] = {}
        self.priority_event_ids: set[str] = set()

    def begin_run(
        self, *, experiment_id: str = "default", is_production: bool = False,
    ) -> None:
        self.last_trace = []
        self.last_funnel = {}
        self.last_scan_details = {}
        self.last_exposure: dict[str, dict[str, Any]] = {}
        self.priority_event_ids = set()
        self.experiment_id = experiment_id
        self.is_production = is_production

    def scan_details(self, event_id: str) -> dict[str, Any]:
        return dict(self.last_scan_details.get(event_id) or {})

    def select(
        self, event_docs: list[tuple[Event, list[Document]]], cache_scope: str,
        now: datetime | None = None,
    ) -> tuple[list[tuple[Event, list[Document]]], str | None]:
        if self.broad_reading_enabled:
            return self._select_broad(event_docs, cache_scope, now)
        return self._select_single(event_docs, cache_scope, now)

    def _select_single(
        self, event_docs: list[tuple[Event, list[Document]]], cache_scope: str,
        now: datetime | None = None,
    ) -> tuple[list[tuple[Event, list[Document]]], str | None]:
        current = now or datetime.now(timezone.utc)
        recent = self._recent_publications(current)
        signature = hashlib.sha256(json.dumps(
            {
                "events": [event.id for event, _ in event_docs],
                "version": self.VERSION,
                "deterministic_weight": self.deterministic_weight,
                "model_weight": self.model_weight,
                "scout_doc_chars": int(self.config.get("scout_doc_chars", 4000)),
            },
            separators=(",", ":"), sort_keys=True,
        ).encode()).hexdigest()
        cache_key = (
            f"scout_selection:{self.stages.prompt_version}:"
            f"{self.VERSION}:{cache_scope}"
        )
        cached = self.repository.get_state(cache_key)
        if cached:
            try:
                payload = json.loads(cached)
                if payload.get("signature") == signature:
                    by_id = {event.id: (event, docs) for event, docs in event_docs}
                    ranked = []
                    for item in payload.get("ranked") or [
                        {"event_id": event_id, "score": None}
                        for event_id in payload.get("event_ids", [])
                    ]:
                        pair = by_id.get(item["event_id"])
                        if pair is None:
                            continue
                        event, docs = pair
                        event = self._with_topic(event, item.get("topic_id"))
                        score = item.get("score")
                        if score is None:
                            score = event.deterministic_score
                        ranked.append((float(score), event, docs))
                    self.last_trace = list(payload.get("trace") or [])
                    self.last_funnel = dict(payload.get("funnel") or {})
                    self.priority_event_ids = {
                        str(item.get("event_id")) for item in self.last_trace
                        if item.get("official_release_priority")
                    }
                    return [
                        (event, docs) for _, event, docs in
                        self._apply_exposure(ranked, recent, current)
                    ], None
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                pass
        try:
            response = self.stages.scout(event_docs, self.settings["topics"])
            by_id = {item.event_id: item for item in response.items}
            ranked: list[tuple[float, Event, list[Document]]] = []
            trace: list[dict[str, Any]] = []
            rejected = 0
            fallback = 0
            priority_candidates: set[str] = set()
            for event, docs in event_docs:
                item = by_id.get(event.id)
                if item is None:
                    fallback += 1
                    ranked.append((event.deterministic_score, event, docs))
                    trace.append(self._trace_item(
                        event, docs, "candidate",
                        "Scout 未返回该事件，保留并使用确定性分数排序",
                        event.deterministic_score,
                    ))
                    continue
                event = self._with_topic(event, item.topic_id)
                self.repository.upsert_event(event)
                if not item.relevant and event.deterministic_score < self.model_reject_floor:
                    rejected += 1
                    trace.append(self._trace_item(
                        event, docs, "scout_rejected", item.reason,
                        event.deterministic_score,
                    ))
                    continue
                model_score = (
                    item.relevance * .30
                    + item.novelty * .25
                    + item.technical_depth * .25
                    + item.industry_impact * .20
                )
                score = (
                    event.deterministic_score * self.deterministic_weight
                    + model_score * self.model_weight
                )
                ranked.append((score, event, docs))
                if self._is_official_release(event, docs, item.industry_impact, item.relevant):
                    priority_candidates.add(event.id)
                traced = self._trace_item(event, docs, "candidate", item.reason, score)
                traced.update({
                    "deterministic_score": round(float(event.deterministic_score), 2),
                    "model_score": round(float(model_score), 2),
                    "model_relevant": bool(item.relevant),
                })
                trace.append(traced)
            ranked.sort(key=lambda item: (item[0], item[1].last_seen), reverse=True)
            priority_limit = max(
                0, int(self.config.get("preferred_official_release_events", 3))
            )
            priority_ranked = [
                event.id for _, event, _ in ranked
                if event.id in priority_candidates
            ]
            self.priority_event_ids = set(priority_ranked[:priority_limit])
            for traced in trace:
                traced["official_release_priority"] = (
                    traced.get("event_id") in self.priority_event_ids
                )
            funnel = {
                "scout_input_events": len(event_docs),
                "scout_reviewed_events": len(by_id),
                "scout_fallback_events": fallback,
                "scout_rejected_events": rejected,
                "scout_retained_events": len(ranked),
                "official_release_priority_events": len(self.priority_event_ids),
            }
            self.last_trace = trace
            self.last_funnel = funnel
            self.repository.set_state(cache_key, json.dumps({
                "signature": signature,
                "event_ids": [event.id for _, event, _ in ranked],
                "ranked": [
                    {
                        "event_id": event.id,
                        "score": score,
                        "topic_id": event.topic_id,
                    }
                    for score, event, _ in ranked
                ],
                "trace": trace,
                "funnel": funnel,
            }))
            return [
                (event, docs) for _, event, docs in
                self._apply_exposure(ranked, recent, current)
            ], None
        except Exception as exc:
            self.last_trace = [
                self._trace_item(
                    event, docs, "candidate",
                    f"Scout 失败，使用确定性排序：{type(exc).__name__}",
                    event.deterministic_score,
                )
                for event, docs in event_docs
            ]
            self.last_funnel = {
                "scout_input_events": len(event_docs),
                "scout_reviewed_events": 0,
                "scout_fallback_events": len(event_docs),
                "scout_rejected_events": 0,
                "scout_retained_events": len(event_docs),
                "official_release_priority_events": 0,
            }
            return self.order_with_repeat(event_docs, current), f"scout: {type(exc).__name__}: {exc}"

    def _select_broad(
        self, event_docs: list[tuple[Event, list[Document]]], cache_scope: str,
        now: datetime | None = None,
    ) -> tuple[list[tuple[Event, list[Document]]], str | None]:
        """Let DeepSeek scan all events, shortlist globally, then rerank once."""
        current = now or datetime.now(timezone.utc)
        recent = self._recent_publications(current)
        signature = self._broad_signature(event_docs)
        cache_key = self._broad_cache_key(cache_scope)
        cached = self.repository.get_state(cache_key)
        if cached:
            restored = self._restore_broad_cache(cached, signature, event_docs, recent, current)
            if restored is not None:
                progress(f"当前：复用 DeepSeek 泛读与复排缓存（{len(restored)} 个候选）")
                return restored, None

        batch_size = max(1, int(self.broad_config.get("batch_size", 30)))
        doc_chars = max(200, int(self.broad_config.get("doc_chars", 1800)))
        errors: list[str] = []

        progress(f"当前：启动 DeepSeek 泛读，扫描全部 {len(event_docs)} 个事件")
        broad_response = ScoutBatch(items=[])
        try:
            broad_response = self.stages.scout(
                event_docs, self.settings["topics"], stage="scout_broad",
                batch_size=batch_size, doc_chars=doc_chars,
                progress_label="DeepSeek 泛读批次",
            )
        except Exception as exc:
            errors.append(f"scout_broad: {type(exc).__name__}: {exc}")

        broad_ranked, broad_trace, broad_stats, broad_priority = (
            self._rank_scout_response(
                event_docs, broad_response, reader="deepseek",
                runner=self.stages, stage="scout_broad", allow_reject=True,
            )
        )
        broad_ranked = self._apply_exposure(broad_ranked, recent, current, record_trace=False)
        shortlist = self._take_shortlist(
            broad_ranked, set(), self.shortlist_events
        )

        rerank_response = ScoutBatch(items=[])
        rerank_failed = False
        if shortlist:
            try:
                progress(f"当前：DeepSeek 复排全局前 {len(shortlist)} 个泛读候选")
                rerank_response = self.stages.scout(
                    shortlist, self.settings["topics"], stage="scout_rerank",
                    batch_size=max(
                        1, int(self.broad_config.get("rerank_batch_size", 40))
                    ),
                    doc_chars=doc_chars,
                    progress_label="DeepSeek 统一复排批次",
                )
            except Exception as exc:
                rerank_failed = True
                errors.append(f"scout_rerank: {type(exc).__name__}: {exc}")

        final_ranked, rerank_trace, rerank_stats, rerank_priority = (
            self._rank_scout_response(
                shortlist, rerank_response, reader="deepseek_rerank",
                runner=self.stages, stage="scout_rerank", allow_reject=False,
            )
        )
        if rerank_failed:
            # Keep the final order stable when the normalization pass is unavailable.
            final_ranked = [
                (event.deterministic_score, event, docs)
                for _, event, docs in final_ranked
            ]
            final_ranked.sort(
                key=lambda item: (item[0], item[1].last_seen), reverse=True
            )

        raw_ranked = list(final_ranked)
        final_ranked = self._apply_exposure(raw_ranked, recent, current)
        priority_limit = max(
            0, int(self.config.get("preferred_official_release_events", 3))
        )
        priority_ranked = [
            event.id for _, event, docs in final_ranked
            if event.id in rerank_priority
            and self._repeat_factor(event, docs, recent) >= 0.99
        ]
        self.priority_event_ids = set(priority_ranked[:priority_limit])

        trace_by_id = {
            str(item["event_id"]): item
            for item in broad_trace
        }
        shortlisted_ids = {event.id for _, event, _ in final_ranked}
        for event_id, traced in trace_by_id.items():
            if traced.get("status") == "candidate" and event_id not in shortlisted_ids:
                traced["status"] = "broad_not_shortlisted"
                traced["reason"] = (
                    f"未进入 DeepSeek 全局前 {self.shortlist_events}；"
                    + str(traced.get("reason") or "")
                )[:500]
        for reranked in rerank_trace:
            event_id = str(reranked["event_id"])
            previous = trace_by_id.get(event_id, {})
            broad_reader = previous.get("broad_reader", "")
            broad_score = previous.get("broad_score", previous.get("score"))
            previous.update(reranked)
            previous.update({
                "status": "candidate",
                "broad_reader": broad_reader,
                "broad_score": broad_score,
                "rerank_score": reranked.get("score"),
                "official_release_priority": event_id in self.priority_event_ids,
            })
            trace_by_id[event_id] = previous

        self.last_trace = [
            {**trace_by_id[event.id], **self.last_exposure.get(event.id, {})}
            for event, _ in event_docs
            if event.id in trace_by_id
        ]
        rerank_by_id = {
            str(item["event_id"]): item for item in rerank_trace
        }
        self.last_scan_details = {
            event_id: {
                "scan": str(item.get("scan") or "")[:220],
                "reason": str(item.get("reason") or "")[:500],
                "model": str(item.get("model") or self.stages.configured_model("scout")),
                "reader": "deepseek_rerank",
                "public_headline": str(item.get("public_headline") or "")[:120],
                "public_summary": str(item.get("public_summary") or "")[:400],
                "public_facts": list(item.get("public_facts") or []),
                "excerpts": dict(item.get("excerpts") or {}),
            }
            for event_id, item in rerank_by_id.items()
            if event_id in shortlisted_ids
        }
        self.last_funnel = {
            "scout_input_events": len(event_docs),
            "scout_reviewed_events": broad_stats["reviewed"],
            "scout_fallback_events": broad_stats["fallback"],
            "scout_rejected_events": broad_stats["rejected"],
            "scout_retained_events": len(final_ranked),
            "broad_reading_input_events": len(event_docs),
            "broad_reading_reviewed_events": broad_stats["reviewed"],
            "broad_reading_fallback_events": broad_stats["fallback"],
            "broad_reading_rejected_events": broad_stats["rejected"],
            "broad_reading_shortlist_events": len(shortlist),
            "rerank_reviewed_events": rerank_stats["reviewed"],
            "rerank_fallback_events": rerank_stats["fallback"],
            "official_release_priority_events": len(self.priority_event_ids),
        }
        self.repository.set_state(cache_key, json.dumps({
            "signature": signature,
            "ranked": [
                {
                    "event_id": event.id,
                    "score": score,
                    "topic_id": event.topic_id,
                }
                for score, event, _ in raw_ranked
            ],
            "trace": self.last_trace,
            "funnel": self.last_funnel,
            "scan_details": self.last_scan_details,
        }, ensure_ascii=False))
        selected = [(event, docs) for _, event, docs in final_ranked]
        return selected, "; ".join(errors) if errors else None

    def _rank_scout_response(
        self,
        event_docs: list[tuple[Event, list[Document]]],
        response: ScoutBatch,
        *,
        reader: str,
        runner: ModelStageRunner,
        stage: str,
        allow_reject: bool,
    ) -> tuple[
        list[tuple[float, Event, list[Document]]],
        list[dict[str, Any]], dict[str, int], set[str],
    ]:
        valid_ids = {event.id for event, _ in event_docs}
        by_id = {
            item.event_id: item for item in response.items
            if item.event_id in valid_ids
        }
        model = self._actual_model(runner, stage)
        ranked: list[tuple[float, Event, list[Document]]] = []
        trace: list[dict[str, Any]] = []
        fallback = 0
        rejected = 0
        priority: set[str] = set()
        for original_event, docs in event_docs:
            item = by_id.get(original_event.id)
            if item is None:
                fallback += 1
                event = original_event
                score = event.deterministic_score
                scan = self._fallback_scan(event, docs)
                reason = f"{reader} 未返回该事件，使用确定性分数"
                relevant = True
                industry_impact = 0.0
                model_score = None
                public_headline = ""
                public_summary = ""
                public_facts: list[dict[str, Any]] = []
                excerpts = {}
            else:
                event = self._with_topic(original_event, item.topic_id)
                self.repository.upsert_event(event)
                score, model_score = self._fusion_score(event, item)
                scan = (item.scan or self._fallback_scan(event, docs)).strip()[:220]
                reason = item.reason
                relevant = bool(item.relevant)
                industry_impact = float(item.industry_impact)
                public_headline = localize_public_text(item.public_headline or "")
                public_summary = localize_public_text(item.public_summary or "")
                public_facts = [
                    fact.model_dump(mode="json")
                    for fact in localize_public_facts(item.public_facts)
                    if fact.text.strip()
                ]
                excerpts = {
                    document.id: (document.content or document.summary or "")[:
                        max(200, int(self.broad_config.get("doc_chars", 1800)))
                    ]
                    for document in docs
                }
                if allow_reject and not relevant and event.deterministic_score < self.model_reject_floor:
                    rejected += 1
                    traced = self._trace_item(
                        event, docs, "scout_rejected", reason,
                        event.deterministic_score,
                    )
                    traced.update({
                        "broad_reader": reader,
                        "model": model,
                        "scan": scan,
                        "model_relevant": False,
                    })
                    trace.append(traced)
                    continue
            ranked.append((float(score), event, docs))
            if self._is_official_release(event, docs, industry_impact, relevant):
                priority.add(event.id)
            traced = self._trace_item(event, docs, "candidate", reason, score)
            traced.update({
                "broad_reader": reader,
                "broad_score": round(float(score), 2),
                "deterministic_score": round(float(event.deterministic_score), 2),
                "model_score": (
                    round(float(model_score), 2) if model_score is not None else None
                ),
                "model_relevant": relevant,
                "model": model,
                "scan": scan,
                "public_headline": public_headline,
                "public_summary": public_summary,
                "public_facts": public_facts,
                "excerpts": excerpts,
            })
            trace.append(traced)
        ranked.sort(key=lambda item: (item[0], item[1].last_seen), reverse=True)
        return ranked, trace, {
            "reviewed": len(by_id),
            "fallback": fallback,
            "rejected": rejected,
        }, priority

    def _fusion_score(self, event: Event, item: ScoutItem) -> tuple[float, float]:
        model_score = (
            item.relevance * .30
            + item.novelty * .25
            + item.technical_depth * .25
            + item.industry_impact * .20
        )
        score = (
            event.deterministic_score * self.deterministic_weight
            + model_score * self.model_weight
        )
        return score, model_score

    @staticmethod
    def _take_shortlist(
        ranked: list[tuple[float, Event, list[Document]]],
        priority_ids: set[str],
        limit: int,
    ) -> list[tuple[Event, list[Document]]]:
        ordered = [item for item in ranked if item[1].id in priority_ids]
        ordered.extend(item for item in ranked if item[1].id not in priority_ids)
        return [(event, docs) for _, event, docs in ordered[:max(0, limit)]]

    def _broad_signature(
        self, event_docs: list[tuple[Event, list[Document]]],
    ) -> str:
        payload = {
            "events": [
                {
                    "event_id": event.id,
                    "documents": [document.content_hash for document in docs],
                }
                for event, docs in event_docs
            ],
            "version": self.VERSION,
            "deterministic_weight": self.deterministic_weight,
            "model_weight": self.model_weight,
            "shortlist_events": self.shortlist_events,
            "batch_size": int(self.broad_config.get("batch_size", 30)),
            "rerank_batch_size": int(self.broad_config.get("rerank_batch_size", 40)),
            "doc_chars": int(self.broad_config.get("doc_chars", 1800)),
        }
        return hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")).hexdigest()

    def _broad_cache_key(self, cache_scope: str) -> str:
        identity = {
            "version": self.VERSION,
            "prompt_version": self.broad_prompt_version,
            "primary": self._runner_identity(self.stages),
        }
        fingerprint = hashlib.sha256(json.dumps(
            identity, ensure_ascii=False, sort_keys=True,
        ).encode("utf-8")).hexdigest()[:12]
        return (
            f"scout_selection:{self.broad_prompt_version}:"
            f"{self.VERSION}:{cache_scope}:{fingerprint}"
        )

    @staticmethod
    def _runner_identity(runner: ModelStageRunner | None) -> dict[str, Any]:
        if runner is None:
            return {}
        metadata = runner.llm.runtime_metadata()
        configured = metadata.get(
            "configured_models", metadata.get("models", {})
        )
        return {
            "provider": metadata.get("provider", type(runner.llm).__name__),
            "base_url": metadata.get("base_url", ""),
            "model": configured.get("scout", runner.configured_model("scout")),
        }

    @staticmethod
    def _actual_model(runner: ModelStageRunner, stage: str) -> str:
        actual = runner.runtime_metadata().get("models", {}).get(stage)
        if isinstance(actual, list):
            return ",".join(str(item) for item in actual)
        return str(actual or runner.configured_model(stage))

    @staticmethod
    def _fallback_scan(event: Event, docs: list[Document]) -> str:
        for document in docs:
            value = (document.summary or document.title).strip()
            if value:
                return value[:220]
        return event.title[:220]

    def _restore_broad_cache(
        self,
        cached: str,
        signature: str,
        event_docs: list[tuple[Event, list[Document]]],
        recent: list[PublicationRecord],
        now: datetime,
    ) -> list[tuple[Event, list[Document]]] | None:
        try:
            payload = json.loads(cached)
            if payload.get("signature") != signature:
                return None
            by_id = {event.id: (event, docs) for event, docs in event_docs}
            ranked: list[tuple[float, Event, list[Document]]] = []
            for item in payload.get("ranked", []):
                pair = by_id.get(str(item.get("event_id") or ""))
                if pair is None:
                    continue
                event, docs = pair
                event = self._with_topic(event, item.get("topic_id"))
                ranked.append((
                    float(item.get("score", event.deterministic_score)), event, docs,
                ))
            self.last_trace = list(payload.get("trace") or [])
            self.last_funnel = dict(payload.get("funnel") or {})
            self.last_scan_details = dict(payload.get("scan_details") or {})
            self.priority_event_ids = {
                str(item.get("event_id")) for item in self.last_trace
                if item.get("official_release_priority")
            }
            return [
                (event, docs) for _, event, docs in
                self._apply_exposure(ranked, recent, now)
            ]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def order_with_repeat(
        self, event_docs: list[tuple[Event, list[Document]]], now: datetime,
    ) -> list[tuple[Event, list[Document]]]:
        recent = self._recent_publications(now)
        scored = [
            (event.deterministic_score, event, docs)
            for event, docs in event_docs
        ]
        adjusted = self._apply_exposure(scored, recent, now)
        if not self.last_trace:
            self.last_trace = [
                self._trace_item(
                    event, docs, "candidate", "AI 未启用，使用确定性排序", score,
                )
                for score, event, docs in adjusted
            ]
            self.last_funnel = {
                "scout_input_events": len(event_docs),
                "scout_reviewed_events": 0,
                "scout_fallback_events": len(event_docs),
                "scout_rejected_events": 0,
                "scout_retained_events": len(event_docs),
                "official_release_priority_events": 0,
            }
        return [(event, docs) for _, event, docs in adjusted]

    def _apply_exposure(
        self,
        ranked: list[tuple[float, Event, list[Document]]],
        recent: list[PublicationRecord],
        now: datetime,
        *,
        record_trace: bool = True,
    ) -> list[tuple[float, Event, list[Document]]] | list[tuple[Event, list[Document]]]:
        adjusted: list[tuple[float, Event, list[Document]]] = []
        for score, event, docs in ranked:
            freshness = self._freshness_factor(docs, now)
            repeat = self._repeat_factor(event, docs, recent)
            adjusted.append((score * freshness * repeat, event, docs))
            self.last_exposure[event.id] = {
                "raw_score": round(float(score), 2),
                "freshness_factor": round(float(freshness), 3),
                "repeat_factor": round(float(repeat), 3),
                "adjusted_score": round(float(score * freshness * repeat), 2),
                "freshness_label": self._freshness_label(event, docs, recent),
            }
        adjusted.sort(key=lambda item: (item[0], item[1].last_seen), reverse=True)
        return adjusted

    def _with_topic(self, event: Event, topic_id: str | None) -> Event:
        resolved = str(topic_id or "")
        if resolved not in self.topic_names:
            resolved = event.topic_id if event.topic_id in self.topic_names else UNCLASSIFIED_TOPIC_ID
        return event.model_copy(update={
            "topic_id": resolved,
            "topic_name": self.topic_names[resolved],
        })

    @staticmethod
    def _is_official_release(
        event: Event, docs: list[Document], industry_impact: float, relevant: bool,
    ) -> bool:
        if not relevant or industry_impact < 65:
            return False
        official_article = any(
            doc.source_tier == 1
            and doc.content_type == "article"
            and str((doc.metadata or {}).get("publisher_id") or doc.source_id) != "arxiv"
            for doc in docs
        )
        if not official_article:
            return False
        return bool(re.search(
            r"\b(?:introducing|launch(?:ed)?|release[ds]?|announc(?:e|ed|es|ing)|unveil(?:ed|s|ing))\b|发布|推出|释出",
            event.title,
            re.IGNORECASE,
        ))

    @staticmethod
    def _trace_item(
        event: Event, docs: list[Document], status: str, reason: str, score: float,
    ) -> dict[str, Any]:
        return {
            "event_id": event.id,
            "title": event.title,
            "topic_id": event.topic_id,
            "lane": event_lane(docs),
            "status": status,
            "score": round(float(score), 2),
            "reason": str(reason or "")[:500],
        }

    def _recent_publications(self, now: datetime) -> list[PublicationRecord]:
        current = _aware(now)
        start_of_today = current.replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff = start_of_today - timedelta(hours=self.repeat_hours)
        production_only = bool(self.is_production)
        try:
            return self.repository.recent_publications(
                cutoff, start_of_today,
                production_only=production_only,
                experiment_id=None if production_only else self.experiment_id,
            )
        except AttributeError:
            return []

    def _repeat_factor(
        self, event: Event, docs: list[Document], recent: list[PublicationRecord],
    ) -> float:
        previous = self._previous_publication(event, docs, recent)
        if previous is None or self.repeat_penalty >= 1:
            return 1.0
        current = content_fingerprint(docs)
        if previous.content_fingerprint and current and previous.content_fingerprint != current:
            return min(1.0, self.repeat_penalty + 0.35)
        return self.repeat_penalty

    def _freshness_factor(self, docs: list[Document], now: datetime) -> float:
        ages = [hours_since(item.published_at, now) for item in docs]
        ages = [item for item in ages if item is not None]
        if not ages:
            return self.freshness_48_72h
        age = min(ages)
        if age <= 24:
            return self.freshness_0_24h
        if age <= 48:
            return self.freshness_24_48h
        return self.freshness_48_72h

    def _freshness_label(
        self, event: Event, docs: list[Document], recent: list[PublicationRecord],
    ) -> str:
        previous = self._previous_publication(event, docs, recent)
        if previous is None:
            return "首次收录"
        current = content_fingerprint(docs)
        if previous.content_fingerprint and current and previous.content_fingerprint != current:
            return "有新材料"
        if not previous.content_fingerprint:
            return "新增材料待核对"
        return "再次收录"

    def _previous_publication(
        self, event: Event, docs: list[Document], recent: list[PublicationRecord],
    ) -> PublicationRecord | None:
        if not recent:
            return None
        urls = set(canonical_urls(docs))
        for item in recent:
            if item.event_id == event.id or (urls and urls & set(item.canonical_urls)):
                return item
        return None

    @staticmethod
    def prioritize_for_research(
        event_docs: list[tuple[Event, list[Document]]],
        preferred_general: int,
        preferred_hardcore: int,
        preferred_max_per_topic: int,
        priority_event_ids: set[str] | None = None,
    ) -> list[tuple[Event, list[Document]]]:
        """Apply lane/topic preferences, then backfill instead of dropping candidates."""
        preferred_by_lane = {
            "general": max(0, preferred_general),
            "hardcore": max(0, preferred_hardcore),
        }
        topic_cap = max(1, preferred_max_per_topic)
        priority_ids = priority_event_ids or set()
        priority_pairs = [pair for pair in event_docs if pair[0].id in priority_ids]
        regular_pairs = [pair for pair in event_docs if pair[0].id not in priority_ids]
        first_by_lane_topic: list[tuple[Event, list[Document]]] = []
        remainder: list[tuple[Event, list[Document]]] = []
        seen_lane_topics: set[tuple[str, str]] = set()
        for pair in regular_pairs:
            key = (event_lane(pair[1]), pair[0].topic_id)
            if key in seen_lane_topics:
                remainder.append(pair)
            else:
                seen_lane_topics.add(key)
                first_by_lane_topic.append(pair)
        coverage_order = first_by_lane_topic + remainder
        chosen: list[tuple[Event, list[Document]]] = list(priority_pairs)
        deferred_lane: list[tuple[Event, list[Document]]] = []
        deferred_topic: list[tuple[Event, list[Document]]] = []
        lane_counts = {"general": 0, "hardcore": 0}
        topic_counts: dict[str, int] = {}
        for event, docs in priority_pairs:
            lane = event_lane(docs)
            lane_counts[lane] += 1
            topic_counts[event.topic_id] = topic_counts.get(event.topic_id, 0) + 1

        for pair in coverage_order:
            event, docs = pair
            lane = event_lane(docs)
            if topic_counts.get(event.topic_id, 0) >= topic_cap:
                deferred_topic.append(pair)
            elif lane_counts[lane] >= preferred_by_lane[lane]:
                deferred_lane.append(pair)
            else:
                chosen.append(pair)
                lane_counts[lane] += 1
                topic_counts[event.topic_id] = topic_counts.get(event.topic_id, 0) + 1

        for pair in deferred_lane:
            event, docs = pair
            if topic_counts.get(event.topic_id, 0) >= topic_cap:
                deferred_topic.append(pair)
                continue
            chosen.append(pair)
            lane = event_lane(docs)
            lane_counts[lane] += 1
            topic_counts[event.topic_id] = topic_counts.get(event.topic_id, 0) + 1

        return chosen + deferred_topic


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
