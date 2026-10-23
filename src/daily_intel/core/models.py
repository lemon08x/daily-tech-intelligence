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


class ClaimStatus(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    CONFLICTING = "conflicting"
    UNVERIFIABLE = "unverifiable"
    UNAUDITED = "unaudited"


class ClaimKind(StrEnum):
    FACT = "fact"
    OUTLOOK = "outlook"


class ResearchStatus(StrEnum):
    DEEP_PASS = "deep_pass"
    QUALITY_DOWNGRADE = "quality_downgrade"
    RESEARCH_FAILED = "research_failed"
    BROAD_ONLY = "broad_only"
    MATERIAL_GATE_EXTENSIVE = "material_gate_extensive"


class PublicationTier(StrEnum):
    INTENSIVE = "intensive"
    EXTENSIVE = "extensive"


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
    snapshot_hash: str = ""
    char_start: int | None = None
    char_end: int | None = None


class IndustryImpact(StrictModel):
    segment: str
    direction: Literal["positive", "negative", "mixed", "uncertain"]
    horizon: Literal["0-6m", "6-12m", "12-24m", "24m+"]
    rationale: str


class ClaimAudit(StrictModel):
    claim_id: str
    field_path: str
    text: str
    status: ClaimStatus = ClaimStatus.UNAUDITED
    kind: ClaimKind = ClaimKind.FACT
    document_id: str = ""
    snapshot_hash: str = ""
    locator: str = ""
    quote: str = ""
    notes: str = ""


class ClaimCheck(StrictModel):
    """Verifier judgement for one program-enumerated claim."""

    claim_id: str = ""
    field_path: str = ""
    text: str = ""
    status: Literal["supported", "unsupported", "conflicting", "unverifiable"] = "unverifiable"
    document_id: str = ""
    locator: str = ""
    quote: str = ""
    notes: str = ""


class ComparisonRow(StrictModel):
    subject: str
    metric: str
    value: float | None = None
    unit: str = ""
    baseline: str = ""
    condition: str = ""
    quote: str = ""
    document_id: str = ""


class AnalysisQuality(StrictModel):
    """Deterministic audit result applied after all model stages."""

    policy_version: str = "evidence-gate-v5"
    passed: bool = False
    score: int = Field(default=0, ge=0, le=100)
    supported_evidence: int = Field(default=0, ge=0)
    primary_sources: int = Field(default=0, ge=0)
    source_diversity: int = Field(default=0, ge=0)
    unsupported_claims: list[str] = Field(default_factory=list, max_length=12)
    issues: list[str] = Field(default_factory=list, max_length=16)
    schema_version: str = "claim-audit-v2"
    claim_audits: list[ClaimAudit] = Field(default_factory=list)
    final_content_passed: bool = False
    final_claim_count: int = 0
    removed_claim_count: int = 0
    source_limitations: list[str] = Field(default_factory=list)
    completeness_issues: list[str] = Field(default_factory=list)


class PublicationMeta(StrictModel):
    selection_order: int = 0
    was_research_target: bool = False
    cache_hit: bool = False
    research_status: str = ""
    publication_tier: str = ""
    material_gate_passed: bool | None = None
    material_gate_reason: str = ""
    public_notice: str = ""
    source_headline: str = ""
    disputed_claims: list[str] = Field(default_factory=list)
    content_fingerprint: str = ""
    snapshot_hash: str = ""
    schema_version: str = "claim-audit-v2"
    source_published_at: datetime | None = None
    source_updated_at: datetime | None = None
    original_published_at: datetime | None = None
    first_published_at: datetime | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    freshness_label: str = ""
    unverified_placeholder: bool = False
    source_excerpts: list[str] = Field(default_factory=list)


class Analysis(StrictModel):
    # extra="ignore" keeps cached analyses from older schema versions (they still
    # carry removed fields such as company_mappings) loadable from SQLite.
    model_config = ConfigDict(extra="ignore")

    event_id: str
    status: AnalysisStatus
    headline: str
    plain_takeaway: str = ""
    scan_sentence: str = ""
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
    quality: AnalysisQuality
    model: str = ""
    prompt_version: str = ""
    created_at: datetime
    lane: Literal["general", "hardcore"] = "hardcore"
    topic_id: str = ""
    topic_name: str = ""
    publication: PublicationMeta = Field(default_factory=PublicationMeta)


class ScoutPublicFact(StrictModel):
    text: str = Field(default="", max_length=300)
    document_id: str = ""
    quote: str = Field(default="", max_length=400)


class ScoutItem(StrictModel):
    event_id: str
    relevant: bool = Field(description="是否有具体阅读价值；仅主题相关、技术复杂或资料可靠不算。")
    topic_id: str
    relevance: float = Field(ge=0, le=100, description="阅读价值：能否帮助读者理解科技方向、发现可用方法或理解商业机会与瓶颈，而非关键词匹配度。")
    novelty: float = Field(ge=0, le=100)
    technical_depth: float = Field(ge=0, le=100)
    industry_impact: float = Field(ge=0, le=100)
    reason: str = Field(description="具体的阅读收获或淘汰理由，不能只说有创新、技术深入或证据充分。")
    scan: str = Field(
        default="",
        max_length=220,
        description="建议60到140字：发生了什么、为什么值得了解；关键数字若出现，保留基线和条件，不作无依据外推。",
    )
    public_headline: str = Field(default="", max_length=120, description="建议20到50字，从具体问题、用途或变化切入，保留研究范围；项目名可放摘要。")
    public_summary: str = Field(default="", max_length=400, description="建议120到240字：先讲问题、变化及阅读价值，再给支撑这一认识的关键结果与主要边界。")
    public_facts: list[ScoutPublicFact] = Field(default_factory=list, max_length=3)


class ScoutBatch(StrictModel):
    items: list[ScoutItem]


class MarketNewsAnalysis(StrictModel):
    title: str
    kicker: str = Field(default="", max_length=8)
    scan: str = Field(min_length=8, max_length=160)


class DigestBrief(StrictModel):
    market_news: list[MarketNewsAnalysis] = Field(default_factory=list, max_length=12)


class GitProjectBrief(StrictModel):
    full_name: str
    # Git briefs are normalized at publication time. Keep the model-facing
    # schema permissive so one slightly verbose label cannot invalidate the
    # entire batch of otherwise useful project summaries.
    kicker: str = ""
    function: str = Field(min_length=8)
    use_cases: list[str] = Field(default_factory=list)


class GitBriefingBatch(StrictModel):
    items: list[GitProjectBrief] = Field(default_factory=list, max_length=12)


class AnalysisDraft(StrictModel):
    headline: str = Field(
        description="建议20到50字，从具体问题、用途或变化切入，保留研究范围；项目名可放正文。",
    )
    # Draft limits are deliberately wider than published limits. The deterministic
    # quality gate normalizes verbose model responses before building Analysis.
    scan_sentence: str = Field(
        default="",
        description="建议60到140字：发生了什么、为什么值得了解；必要数字与基线及适用条件紧邻。",
    )
    plain_takeaway: str = Field(
        min_length=8,
        description="建议120到240字：先讲问题、本次变化及其意义，再给支撑这个认识的关键结果和主要边界。保留项目名；解释依据文档，不强行推导商业收益。",
    )
    key_facts: list[str] = Field(
        default_factory=list,
        max_length=16,
        description="每条先写人能看懂的结论，再跟必要数字或专名。不要把论文摘要或变更列表原样搬进来。",
    )
    technical_mechanism: str = Field(
        description="先用人话讲清机制，再保留必要术语；缩写首次出现必须解释。",
    )
    novelty: str
    maturity: str
    outlook_6_24m: str
    industry_impacts: list[IndustryImpact] = Field(default_factory=list, max_length=16)
    risks: list[str] = Field(default_factory=list, max_length=16)
    counterpoints: list[str] = Field(default_factory=list, max_length=16)
    confidence: float = Field(ge=0, le=1)
    evidence: list[Evidence] = Field(default_factory=list, max_length=24)
    claims: list[ClaimAudit] = Field(
        default_factory=list,
        description="模型可选自报的断言清单，不能代替程序核对字段覆盖。",
    )

    @field_validator("evidence")
    @classmethod
    def evidence_document_ids_are_nonempty(cls, value: list[Evidence]) -> list[Evidence]:
        if any(not item.document_id.strip() for item in value):
            raise ValueError("evidence.document_id must not be empty")
        return value


class VerificationResult(StrictModel):
    supported_evidence_indexes: list[int] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    claim_checks: list[ClaimCheck] = Field(default_factory=list)
    comparison_rows: list[ComparisonRow] = Field(default_factory=list)
    confidence_adjustment: float = Field(ge=-0.5, le=0.2)
    verdict: Literal["pass", "downgrade", "reject"]
    notes: str
    schema_version: str = "claim-audit-v2"


class PublicationRecord(StrictModel):
    run_id: int
    event_id: str
    canonical_urls: list[str] = Field(default_factory=list)
    experiment_id: str = ""
    is_production: bool = False
    selection_order: int = 0
    publication_tier: str = ""
    research_status: str = ""
    published_at: datetime
    content_fingerprint: str = ""
    freshness_label: str = ""
