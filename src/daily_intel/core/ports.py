from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar

import pandas as pd
from pydantic import BaseModel

from daily_intel.core.models import Analysis, Document, Event


class SourceAdapter(Protocol):
    source_id: str

    def collect(self, since: datetime, limit: int) -> list[Document]: ...


T = TypeVar("T", bound=BaseModel)


class LLMResult(Generic[T]):
    def __init__(
        self, value: T, model: str, input_tokens: int = 0, output_tokens: int = 0,
        *, usage_estimated: bool = False,
    ) -> None:
        self.value = value
        self.model = model
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.usage_estimated = usage_estimated


class LLMClient(ABC):
    @property
    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def generate(self, stage: str, system: str, user: str, schema: type[T]) -> LLMResult[T]: ...

    def runtime_metadata(self) -> dict[str, Any]:
        """Describe the active client instead of echoing static YAML values."""
        return {
            "provider": type(self).__name__,
            "configured_models": {},
            "usage_reporting": "unknown",
        }


class IntelligenceRepository(Protocol):
    def upsert_document(self, document: Document) -> bool: ...
    def update_document_content(self, document: Document) -> None: ...
    def get_documents(self, ids: list[str]) -> list[Document]: ...
    def recent_documents(self, since: datetime) -> list[Document]: ...
    def upsert_event(self, event: Event) -> None: ...
    def get_analysis(self, event_id: str, cache_scope: str = "default") -> Analysis | None: ...
    def save_analysis(self, analysis: Analysis, cache_scope: str = "default") -> None: ...
    def get_latest_analyses(
        self, limit: int, cache_scope: str | None = None,
    ) -> list[Analysis]: ...
    def record_llm_run(
        self, stage: str, event_id: str | None, model: str, prompt_version: str,
        input_tokens: int, output_tokens: int, status: str, error: str = "",
    ) -> None: ...
    def get_state(self, key: str) -> str | None: ...
    def set_state(self, key: str, value: str) -> None: ...
    def start_run(self, metadata: dict[str, Any]) -> int: ...
    def finish_run(self, run_id: int, status: str, metadata: dict[str, Any]) -> None: ...


class MarketProvider(Protocol):
    def industries(self) -> Any: ...
    def indices(self) -> Any: ...
    def news(self) -> Any: ...
    def trading_calendar(self) -> Any: ...
    def global_indices(self) -> Any: ...
    def global_futures(self) -> Any: ...


class MarketWorkflow(Protocol):
    def run(self) -> Any: ...


class IntelligenceWorkflow(Protocol):
    def run(
        self, now: datetime, radar_news: pd.DataFrame,
        offline: bool = False, no_ai: bool = False, require_ai: bool = False,
        experiment_id: str = "default", force_analysis: bool = False,
    ) -> Any: ...


class DigestPublisher(Protocol):
    def publish(
        self, context: dict[str, Any], analyses: list[Analysis],
        metadata: dict[str, Any], output_dir: Path, now: datetime,
    ) -> dict[str, Path]: ...
