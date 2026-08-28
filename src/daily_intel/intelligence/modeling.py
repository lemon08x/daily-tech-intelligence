from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from pydantic import BaseModel

from daily_intel.core.models import (
    AnalysisDraft,
    Document,
    Event,
    GitBriefingBatch,
    ScoutBatch,
    VerificationResult,
)
from daily_intel.core.ports import IntelligenceRepository, LLMClient, LLMResult
from daily_intel.core.runs import sanitize_run_identifier
from daily_intel.intelligence.prompts import (
    ANALYST_SYSTEM,
    GIT_BRIEF_SYSTEM,
    SCOUT_SYSTEM,
    VERIFIER_SYSTEM,
    analyst_user,
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
        self._usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0, "estimated": False}
        self._actual_models: dict[str, set[str]] = defaultdict(set)

    @property
    def available(self) -> bool:
        return self.llm.available

    @property
    def usage(self) -> dict[str, Any]:
        return dict(self._usage)

    def begin_run(self) -> None:
        """Reset per-run audit state without discarding persistent LLM history."""
        self._usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "calls": 0,
            "estimated": False,
        }
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
            "experiment_id": sanitize_run_identifier(experiment_id),
            "provider": metadata.get("provider", type(self.llm).__name__),
            "configured_models": configured_models,
            "prompt_version": self.prompt_version,
            "quality_policy": self.quality_policy.version,
        }
        fingerprint = hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        return f"{identity['experiment_id']}-{fingerprint}"

    def scout(self, event_docs: list[tuple[Event, list[Document]]], topics: list[dict]) -> ScoutBatch:
        return self._generate(
            "scout", SCOUT_SYSTEM, scout_user(event_docs, topics), ScoutBatch, None
        ).value

    def brief_github(self, projects: list[dict[str, Any]]) -> GitBriefingBatch:
        return self._generate(
            "git_brief", GIT_BRIEF_SYSTEM, git_brief_user(projects), GitBriefingBatch, None
        ).value

    def analyze(self, event: Event, documents: list[Document]) -> tuple[AnalysisDraft, str]:
        result = self._generate(
            "analyst",
            ANALYST_SYSTEM,
            analyst_user(event, documents, self.quality_policy.prompt_contract()),
            AnalysisDraft,
            event.id,
        )
        return result.value, result.model

    def verify(
        self, event: Event, documents: list[Document], draft: AnalysisDraft,
    ) -> VerificationResult:
        return self._generate(
            "verifier", VERIFIER_SYSTEM, verifier_user(event, documents, draft),
            VerificationResult, event.id,
        ).value

    def _generate(
        self, stage: str, system: str, user: str, schema: type[BaseModel], event_id: str | None,
    ) -> LLMResult:
        error: Exception | None = None
        for _ in range(2):
            try:
                result = self.llm.generate(stage, system, user, schema)
                self.repository.record_llm_run(
                    stage, event_id, result.model, self.prompt_version,
                    result.input_tokens, result.output_tokens, "success",
                )
                self._usage["input_tokens"] += result.input_tokens
                self._usage["output_tokens"] += result.output_tokens
                self._usage["calls"] += 1
                self._usage["estimated"] = self._usage["estimated"] or result.usage_estimated
                self._actual_models[stage].add(result.model)
                return result
            except Exception as exc:
                error = exc
        model = self.settings["llm"].get(stage, {}).get("model", "unknown")
        self.repository.record_llm_run(
            stage, event_id, model, self.prompt_version, 0, 0, "failed", str(error),
        )
        raise error or RuntimeError("LLM调用失败")
