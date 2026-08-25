from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalysisStatus(StrEnum):
    DEEP = "deep"
    LEAD = "lead"
    FAILED = "failed"


class MappingStatus(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"


class Document(StrictModel):
    id: str
    source_id: str
    source_name: str
    external_id: str
    title: str
    url: str
    canonical_url: str
    published_at: datetime
    fetched_at: datetime
    summary: str = ""
    content: str = ""
    content_hash: str
    source_tier: int = Field(ge=1, le=3)
    content_type: str = "article"
    extraction_quality: Literal["full", "summary", "metadata"] = "summary"
    metadata: dict[str, Any] = Field(default_factory=dict)


class Event(StrictModel):
    id: str
    title: str
    topic_id: str
    topic_name: str
    document_ids: list[str]
    first_seen: datetime
    last_seen: datetime
    source_quality: float = Field(ge=0, le=100)
    deterministic_score: float = Field(ge=0, le=100)


class Evidence(StrictModel):
    document_id: str
    url: str
    quote: str = Field(min_length=8, max_length=1200)
    locator: str = "正文"


class IndustryImpact(StrictModel):
    segment: str
    direction: Literal["positive", "negative", "mixed", "uncertain"]
    horizon: Literal["0-6m", "6-12m", "12-24m", "24m+"]
    rationale: str


class CompanyMapping(StrictModel):
    code: str = Field(pattern=r"^\d{6}$")
    name: str
    industry: str = ""
    rationale: str
    status: MappingStatus = MappingStatus.UNVERIFIED
    confidence: float = Field(ge=0, le=1)
    evidence: list[Evidence] = Field(default_factory=list)


class AnalysisQuality(StrictModel):
    """Deterministic audit result applied after all model stages."""

    policy_version: str = "legacy"
    passed: bool = False
    score: int = Field(default=0, ge=0, le=100)
    supported_evidence: int = Field(default=0, ge=0)
    primary_sources: int = Field(default=0, ge=0)
    source_diversity: int = Field(default=0, ge=0)
    unsupported_claims: list[str] = Field(default_factory=list, max_length=12)
    issues: list[str] = Field(default_factory=list, max_length=12)


class Analysis(StrictModel):
    event_id: str
    status: AnalysisStatus
    headline: str
    key_facts: list[str] = Field(default_factory=list, max_length=8)
    technical_mechanism: str = ""
    novelty: str = ""
    maturity: str = ""
    outlook_6_24m: str = ""
    industry_impacts: list[IndustryImpact] = Field(default_factory=list, max_length=8)
    risks: list[str] = Field(default_factory=list, max_length=8)
    counterpoints: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(ge=0, le=1)
    evidence: list[Evidence] = Field(default_factory=list, max_length=12)
    company_mappings: list[CompanyMapping] = Field(default_factory=list, max_length=3)
    quality: AnalysisQuality = Field(default_factory=AnalysisQuality)
    model: str = ""
    prompt_version: str = ""
    created_at: datetime


class MarketSignal(StrictModel):
    """Reserved contract for a future Qlib/RD-Agent adapter."""

    event_id: str
    signal_name: str
    direction: Literal["positive", "negative", "neutral"]
    confidence: float = Field(ge=0, le=1)
    generated_at: datetime


class Digest(StrictModel):
    generated_at: datetime
    market_date: str
    analyses: list[Analysis]
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScoutItem(StrictModel):
    event_id: str
    relevant: bool
    topic_id: str
    relevance: float = Field(ge=0, le=100)
    novelty: float = Field(ge=0, le=100)
    technical_depth: float = Field(ge=0, le=100)
    industry_impact: float = Field(ge=0, le=100)
    reason: str


class ScoutBatch(StrictModel):
    items: list[ScoutItem]


class CompanyHypothesis(StrictModel):
    code: str = Field(pattern=r"^\d{6}$")
    name: str
    rationale: str
    keywords: list[str] = Field(default_factory=list, max_length=5)
    confidence: float = Field(ge=0, le=1)


class AnalysisDraft(StrictModel):
    headline: str
    # Draft limits are deliberately wider than published limits. The deterministic
    # quality gate normalizes verbose model responses before building Analysis.
    key_facts: list[str] = Field(default_factory=list, max_length=16)
    technical_mechanism: str
    novelty: str
    maturity: str
    outlook_6_24m: str
    industry_impacts: list[IndustryImpact] = Field(default_factory=list, max_length=16)
    risks: list[str] = Field(default_factory=list, max_length=16)
    counterpoints: list[str] = Field(default_factory=list, max_length=16)
    confidence: float = Field(ge=0, le=1)
    evidence: list[Evidence] = Field(default_factory=list, max_length=24)
    company_hypotheses: list[CompanyHypothesis] = Field(default_factory=list, max_length=3)

    @field_validator("evidence")
    @classmethod
    def evidence_document_ids_are_nonempty(cls, value: list[Evidence]) -> list[Evidence]:
        if any(not item.document_id.strip() for item in value):
            raise ValueError("evidence.document_id must not be empty")
        return value


class VerificationResult(StrictModel):
    supported_evidence_indexes: list[int] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    confidence_adjustment: float = Field(ge=-0.5, le=0.2)
    verdict: Literal["pass", "downgrade", "reject"]
    notes: str
