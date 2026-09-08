from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from collections import defaultdict
from time import perf_counter
from typing import Any

from pydantic import BaseModel

from daily_intel.core.models import (
    AnalysisDraft, ClaimAudit,
    DigestBrief,
    Document,
    Event,
    GitBriefingBatch,
    ScoutBatch,
    VerificationResult,
)
from daily_intel.core.ports import IntelligenceRepository, LLMClient, LLMResult
from daily_intel.core.progress import progress
from daily_intel.core.runs import sanitize_run_identifier
from daily_intel.intelligence.prompts import (
    ANALYST_SYSTEM,
    DIGEST_BRIEF_SYSTEM,
    GIT_BRIEF_SYSTEM,
    SCOUT_SYSTEM,
    VERIFIER_SYSTEM,
    analyst_user,
    digest_brief_user,
    git_brief_user,
    scout_user,
    verifier_user,
)
from daily_intel.intelligence.quality import QualityPolicy


class ModelStageRunner:
    """Owns model I/O, retry, usage and audit metadata for all AI stages."""

    def __init__(
        self, settings: dict[str, Any], repository: IntelligenceRepository,
        llm: LLMClient, quality_policy: QualityPolicy,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.llm = llm
        self.quality_policy = quality_policy
        self.prompt_version = settings["llm"]["prompt_version"]
        self.audit_context: dict[str, Any] = {}
        self._usage = self._empty_usage()
        self._actual_models: dict[str, set[str]] = defaultdict(set)

    @staticmethod
    def _empty_usage() -> dict[str, Any]:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "calls": 0,
            "attempts": 0,
            "duration_seconds": 0.0,
            "estimated": False,
            "usage_unknown": False,
            "by_stage": {},
        }

    @property
    def available(self) -> bool:
        return self.llm.available

    @property
    def usage(self) -> dict[str, Any]:
        return deepcopy(self._usage)

    def begin_run(self) -> None:
        """Reset per-run audit state without discarding persistent LLM history."""
        self._usage = self._empty_usage()
        self._actual_models.clear()

    def runtime_metadata(self) -> dict[str, Any]:
        metadata = dict(self.llm.runtime_metadata())
        configured = dict(
            metadata.pop("models", metadata.get("configured_models", {}))
        )
        actual = {
            stage: sorted(models)[0] if len(models) == 1 else sorted(models)
            for stage, models in self._actual_models.items()
        }
        metadata["configured_models"] = configured
        metadata["models"] = actual
        metadata["usage_reporting"] = "estimated" if self._usage["estimated"] else metadata.get(
            "usage_reporting", "reported"
        )
        return metadata

    def cache_scope(self, experiment_id: str) -> str:
        metadata = self.llm.runtime_metadata()
        configured_models = metadata.get(
            "configured_models", metadata.get("models", {})
        )
        identity = {
            "clustering_version": "event-identity-v2",
            "claim_contract_version": "fixed-claims-v2",
            "experiment_id": sanitize_run_identifier(experiment_id),
            "provider": metadata.get("provider", type(self.llm).__name__),
            "configured_models": configured_models,
            "prompt_version": self.prompt_version,
            "quality_policy": self.quality_policy.version,
            "schema_version": getattr(self.quality_policy, "schema_version", "claim-audit-v1"),
        }
        fingerprint = hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        return f"{identity['experiment_id']}-{fingerprint}"

    def _source_chars(self, key: str, default: int) -> int:
        return int(self.settings.get("intelligence", {}).get(key, default))

    def configured_model(self, stage: str) -> str:
        stage_config = self.settings["llm"].get(stage) or self.settings["llm"].get(
            "scout", {}
        )
        return str(stage_config.get("model", "unknown"))

    def scout(
        self,
        event_docs: list[tuple[Event, list[Document]]],
        topics: list[dict],
        *,
        stage: str = "scout",
        batch_size: int | None = None,
        doc_chars: int | None = None,
        progress_label: str | None = None,
    ) -> ScoutBatch:
        resolved_batch_size = max(
            1,
            int(batch_size) if batch_size is not None
            else self._source_chars("scout_batch_size", 30),
        )
        resolved_doc_chars = (
            int(doc_chars) if doc_chars is not None
            else self._source_chars("scout_doc_chars", 4000)
        )
        items = []
        total_batches = (
            (len(event_docs) + resolved_batch_size - 1) // resolved_batch_size
            if event_docs else 0
        )
        for batch_number, start in enumerate(
            range(0, len(event_docs), resolved_batch_size), start=1,
        ):
            batch = event_docs[start:start + resolved_batch_size]
            if progress_label:
                progress(
                    f"当前：{progress_label} {batch_number}/{total_batches} "
                    f"（{len(batch)} 个事件）"
                )
            result = self._generate(
                stage, SCOUT_SYSTEM,
                scout_user(batch, topics, doc_chars=resolved_doc_chars),
                ScoutBatch, None,
            ).value
            items.extend(result.items)
        return ScoutBatch(items=items)

    def brief_digest(self, payload: dict[str, Any]) -> DigestBrief:
        return self._generate(
            "digest_brief", DIGEST_BRIEF_SYSTEM, digest_brief_user(payload), DigestBrief, None
        ).value

    def brief_github(self, projects: list[dict[str, Any]]) -> GitBriefingBatch:
        return self._generate(
            "git_brief", GIT_BRIEF_SYSTEM, git_brief_user(projects), GitBriefingBatch, None
        ).value

    def analyze(self, event: Event, documents: list[Document]) -> tuple[AnalysisDraft, str]:
        result = self._generate(
            "analyst",
            ANALYST_SYSTEM,
            analyst_user(
                event, documents, self.quality_policy.prompt_contract(),
                max_chars=self._source_chars("full_text_max_chars", 50000),
            ),
            AnalysisDraft,
            event.id,
        )
        return result.value, result.model

    def verify(
        self, event: Event, documents: list[Document], draft: AnalysisDraft,
        *, claims: list[ClaimAudit] | None = None,
    ) -> VerificationResult:
        return self._generate(
            "verifier", VERIFIER_SYSTEM,
            verifier_user(
                event, documents, draft, claims=claims,
                max_chars=self._source_chars("full_text_max_chars", 50000),
            ),
            VerificationResult, event.id,
        ).value

    def audit(self, event_id: str | None, stage: str, payload: dict, *,
              model: str = "", snapshot_hash: str = "") -> None:
        self.repository.save_research_audit({
            **self.audit_context, "event_id": event_id or "", "stage": stage,
            "model": model, "prompt_version": self.prompt_version,
            "policy_version": self.quality_policy.version,
            "schema_version": self.quality_policy.schema_version,
            "snapshot_hash": snapshot_hash, "payload": payload,
        })

    def _generate(
        self, stage: str, system: str, user: str, schema: type[BaseModel], event_id: str | None,
    ) -> LLMResult:
        # One owner: exactly one initial request and at most one retry; SDK/client do not retry.
        input_hash = self.repository.save_content_snapshot(json.dumps(
            {"system": system, "user": user, "schema": schema.model_json_schema()}, ensure_ascii=False,
        ))
        self.audit(event_id, stage + "_input", {"input_hash": input_hash}, snapshot_hash=input_hash)
        error = None
        stage_usage = self._usage["by_stage"].setdefault(stage, {
            "calls": 0, "attempts": 0, "input_tokens": 0, "output_tokens": 0,
            "duration_seconds": 0.0, "usage_unknown": False,
        })
        for attempt in range(1, 3):
            started = perf_counter()
            result = None
            try:
                result = self.llm.generate(stage, system, user, schema)
            except Exception as exc:
                error = exc
            elapsed = perf_counter() - started
            outcome = result if result is not None else error
            model = getattr(outcome, "model", "") or self.configured_model(stage)
            input_tokens = getattr(outcome, "input_tokens", 0)
            output_tokens = getattr(outcome, "output_tokens", 0)
            unknown = getattr(outcome, "usage_unknown", result is None)
            category = "success" if result is not None else getattr(error, "category", "network_or_client_error")
            for usage in (self._usage, stage_usage):
                usage["attempts"] += 1
                usage["input_tokens"] += input_tokens
                usage["output_tokens"] += output_tokens
                usage["duration_seconds"] = round(usage["duration_seconds"] + elapsed, 3)
                usage["usage_unknown"] = usage.get("usage_unknown", False) or unknown
                if result is not None:
                    usage["calls"] += 1
            self._usage["estimated"] |= unknown or bool(getattr(outcome, "usage_estimated", False))
            if getattr(outcome, "model", ""):
                self._actual_models[stage].add(model)
            self.repository.record_llm_run(stage, event_id, model, self.prompt_version,
                input_tokens, output_tokens, "success" if result is not None else "failed", category if result is None else "")
            payload = {"attempt": attempt, "category": category, "input_tokens": input_tokens,
                       "output_tokens": output_tokens, "usage_unknown": unknown,
                       "finish_reason": getattr(outcome, "finish_reason", ""), "duration_seconds": elapsed}
            if result is not None:
                payload["result"] = result.value.model_dump(mode="json")
            self.audit(event_id, stage + "_attempt", payload, model=model, snapshot_hash=input_hash)
            if result is not None:
                return result
        raise error or RuntimeError("LLM调用失败")
