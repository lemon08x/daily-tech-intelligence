from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pandas as pd
import pytest
from pydantic import BaseModel

from daily_intel.core.models import ClaimAudit, ClaimCheck, ClaimStatus, ComparisonRow, VerificationResult
from daily_intel.infrastructure.llm.openai_compatible import OpenAICompatibleLLM, ModelResponseError
from daily_intel.intelligence.claims import apply_claim_checks, check_numeric_claims, split_sentences
from daily_intel.intelligence.clustering import cluster_documents
from daily_intel.intelligence.modeling import ModelStageRunner
from daily_intel.intelligence.quality import QualityPolicy, summarize_quality
from daily_intel.intelligence.reading import partition_reading_analyses
from test_intelligence_pipeline import FakeLLM, _pipeline, document, settings, NOW


def test_weekly_thirty_distinct_submissions_do_not_merge_even_with_similar_titles():
    docs = [document().model_copy(update={
        "id": str(i), "title": f"开源工具投稿 {i}",
        "url": f"https://github.com/ruanyf/weekly/issues/{11523+i}",
        "canonical_url": f"https://github.com/ruanyf/weekly/issues/{11523+i}",
    }) for i in range(30)]
    assert len(cluster_documents(docs, settings()["topics"], 72, 80)) == 30


def test_two_releases_are_distinct_events():
    docs = [document().model_copy(update={
        "id": str(i), "title": f"vLLM release {i}", "content_type": "github_release",
        "url": f"https://github.com/vllm-project/vllm/releases/tag/v{i}",
        "canonical_url": f"https://github.com/vllm-project/vllm/releases/tag/v{i}",
    }) for i in range(2)]
    assert len(cluster_documents(docs, settings()["topics"], 72, 80)) == 2


def test_chinese_sentences_keep_decimals_abbreviations_and_urls():
    assert split_sentences("得分0.89。下一句！最后一句？") == ["得分0.89。", "下一句！", "最后一句？"]
    assert split_sentences("Dr. Smith uses e.g. GPU. Next result.") == ["Dr. Smith uses e.g. GPU.", "Next result."]
    assert split_sentences("见https://x.org/a?q=1.2。下一句。") == ["见https://x.org/a?q=1.2。", "下一句。"]


@pytest.mark.parametrize("updates,reason", [
    ({"field_path": "key_facts[1]"}, "字段路径失效"),
    ({"text": "another fact"}, "审核文本与草稿不一致"),
    ({"claim_id": "invented"}, "审核结果缺失"),
])
def test_audit_identity_is_strict(updates, reason):
    claim = ClaimAudit(claim_id="key_facts.0", field_path="key_facts[0]", text="one fact")
    check = ClaimCheck(claim_id=claim.claim_id, field_path=claim.field_path, text=claim.text, status="supported")
    verdict = VerificationResult(verdict="pass", confidence_adjustment=0, notes="", claim_checks=[check.model_copy(update=updates)])
    result = apply_claim_checks([claim], verdict)[0]
    assert result.status == ClaimStatus.UNAUDITED and result.notes == reason


def test_duplicate_claims_are_diagnosed():
    claim = ClaimAudit(claim_id="headline", field_path="headline", text="fact")
    check = ClaimCheck(claim_id="headline", field_path="headline", text="fact", status="supported")
    result = apply_claim_checks([claim], VerificationResult(verdict="pass", confidence_adjustment=0, notes="", claim_checks=[check, check]))[0]
    assert result.status == ClaimStatus.UNAUDITED and result.notes == "断言编号重复"


def test_compact_claim_check_binds_text_and_path_from_fixed_inventory():
    claim = ClaimAudit(claim_id="key_facts.2", field_path="key_facts[2]", text="original statement")
    check = ClaimCheck(claim_id=claim.claim_id, status="supported")
    result = apply_claim_checks([claim], VerificationResult(verdict="pass", confidence_adjustment=0, notes="", claim_checks=[check]))[0]
    assert result.status == ClaimStatus.SUPPORTED
    assert result.text == claim.text and result.field_path == claim.field_path


def test_real_money_claim_does_not_compare_url_or_unrelated_numbers():
    source = "MS Hwang 称过去一年智能手机使用的 DRAM 价格上涨最高 250 美元，他预计这笔费用将在今年秋季转嫁到手机价格上。设备销量达5000万。https://www.ft.com/content/ea9a9dcc-b1df-49b0-b80c-f320161b9efa"
    claim = ClaimAudit(claim_id="k", field_path="k", status="supported",
        text="智能手机使用的DRAM价格过去一年上涨最高250美元，预计秋季转嫁到手机价格。")
    assert check_numeric_claims([claim], [document().model_copy(update={"content": source, "summary": source})])[0].status == ClaimStatus.SUPPORTED


def test_comparison_conditions_filter_other_benchmarks():
    body = """Dataset A
| Model | F1 |
| --- | --- |
| Ours | 0.89 |
| Peer | 0.85 |

Dataset B
| Model | F1 |
| --- | --- |
| Ours | 0.70 |
| Peer | 0.99 |
"""
    claim = ClaimAudit(claim_id="k", field_path="k", text="Ours 在 Dataset A 的 F1 领先", status="supported")
    docs = [document().model_copy(update={"content": body, "summary": ""})]
    assert check_numeric_claims([claim], docs)[0].status == ClaimStatus.SUPPORTED
    assert check_numeric_claims([claim.model_copy(update={"text": "Ours 的 F1 领先"})], docs)[0].status == ClaimStatus.UNVERIFIABLE


def test_invented_comparison_rows_cannot_establish_leadership():
    claim = ClaimAudit(claim_id="k", field_path="k", text="Ours 的 F1 领先", status="supported")
    rows = [ComparisonRow(subject=name, metric="F1", value=value, document_id="doc-1", quote="invented", unit="ratio", condition="test") for name, value in [("Ours", .99), ("Peer", .1)]]
    assert check_numeric_claims([claim], [document()], rows)[0].status == ClaimStatus.UNVERIFIABLE


class Answer(BaseModel):
    answer: int


def response(content, reason="stop", usage=True):
    return SimpleNamespace(model="actual-model", choices=[SimpleNamespace(
        message=SimpleNamespace(content=content), finish_reason=reason)],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7) if usage else None)


def client_with_responses(responses):
    client = OpenAICompatibleLLM({"api_key_env": "REMEDIATION_UNUSED_KEY", "base_url": "https://example.invalid", "scout": {"model": "configured"}})
    calls = []
    def create(**request):
        calls.append(request)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return client, calls


def test_array_prefix_and_schema_incompatible_object_are_skipped():
    client, calls = client_with_responses([response('说明 [0] {"wrong": 1} 最终 {"answer": 42}')])
    assert client.generate("scout", "system", "user", Answer).value.answer == 42
    assert len(calls) == 1


@pytest.mark.parametrize("content,reason,category", [
    ("", "stop", "empty_response"),
    ('{"answer":42}', "length", "truncated_response"),
    ('{"broken": {"answer":42}', "stop", "json_syntax"),
    ('{"wrong":42}', "stop", "schema_mismatch"),
    ('[0]', "stop", "schema_mismatch"),
])
def test_parse_failures_keep_usage_and_do_not_retry_in_client(content, reason, category):
    client, calls = client_with_responses([response(content, reason)])
    with pytest.raises(ModelResponseError) as error:
        client.generate("scout", "system", "user", Answer)
    assert error.value.category == category
    assert error.value.input_tokens == 11 and error.value.output_tokens == 7
    assert len(calls) == 1


def test_retry_limit_and_failed_response_tokens_are_recorded(tmp_path):
    from daily_intel.infrastructure.storage.sqlite import SQLiteIntelligenceRepository
    repository = SQLiteIntelligenceRepository(tmp_path / "retry.db")
    client, calls = client_with_responses([response(""), response('{"answer":42}')])
    runner = ModelStageRunner(settings(), repository, client, QualityPolicy())
    assert runner._generate("scout", "s", "u", Answer, "event").value.answer == 42
    assert len(calls) == runner.usage["attempts"] == 2
    assert runner.usage["input_tokens"] == 22 and runner.usage["output_tokens"] == 14
    with sqlite3.connect(tmp_path / "retry.db") as db:
        assert db.execute("SELECT count(*) FROM research_audits WHERE stage='scout_attempt'").fetchone()[0] == 2
    client, calls = client_with_responses([RuntimeError("network"), RuntimeError("network")])
    runner = ModelStageRunner(settings(), repository, client, QualityPolicy())
    with pytest.raises(RuntimeError):
        runner._generate("scout", "s", "u", Answer, "event")
    assert len(calls) == 2 and runner.usage["usage_unknown"]


def test_pipeline_persists_inputs_draft_verification_and_publication(tmp_path, monkeypatch):
    pipeline = _pipeline(tmp_path, FakeLLM(), monkeypatch)
    result = pipeline.run(NOW, pd.DataFrame(), experiment_id="sept08-test")
    with sqlite3.connect(tmp_path / "intel.db") as db:
        rows = db.execute("SELECT stage,snapshot_hash,payload_json FROM research_audits").fetchall()
        stages = {row[0] for row in rows}
        assert {"analyst_input", "analyst_attempt", "verifier_input", "verifier_attempt", "quality_decision", "public_result"} <= stages
        for stage, digest, raw in rows:
            if stage.endswith("_input"):
                snapshot = db.execute("SELECT text FROM content_snapshots WHERE hash=?", (digest,)).fetchone()[0]
                if stage == "verifier_input":
                    payload = json.loads(json.loads(snapshot)["user"])
                    assert payload["claim_inventory"]
        publication = next(json.loads(raw)["analysis"]["publication"] for stage, _, raw in rows if stage == "public_result")
        assert publication["selection_order"] == 1 and publication["publication_tier"]
    assert result.analyses[0].quality.passed


def test_failed_verifier_keeps_analyst_and_input_audits(tmp_path, monkeypatch):
    class FailedVerifier(FakeLLM):
        def generate(self, stage, system, user, schema):
            if stage == "verifier":
                raise RuntimeError("failed")
            return super().generate(stage, system, user, schema)
    pipeline = _pipeline(tmp_path, FailedVerifier(), monkeypatch)
    pipeline.run(NOW, pd.DataFrame())
    with sqlite3.connect(tmp_path / "intel.db") as db:
        assert db.execute("SELECT count(*) FROM research_audits WHERE stage='analyst_attempt'").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM research_audits WHERE stage='verifier_attempt'").fetchone()[0] == 2


def test_broad_facts_need_evidence_and_never_get_first_three_fallback(tmp_path, monkeypatch):
    pipeline = _pipeline(tmp_path, FakeLLM(), monkeypatch)
    doc = document()
    event = cluster_documents([doc], settings()["topics"], 72, 80)[0]
    monkeypatch.setattr(pipeline.selector, "scan_details", lambda _: {
        "public_facts": [{"text": "wrong fact", "document_id": doc.id, "quote": "not in material"}],
        "excerpts": {doc.id: doc.content},
    })
    lead = pipeline._broad_lead(event, [doc], NOW, failure="failed")
    assert not lead.key_facts and not lead.evidence
    intensive, extensive = partition_reading_analyses([lead], 10)
    assert not intensive and extensive == [lead]


def test_research_status_and_material_tier_have_orthogonal_counts(tmp_path, monkeypatch):
    pipeline = _pipeline(tmp_path, FakeLLM(), monkeypatch)
    analysis = pipeline.run(NOW, pd.DataFrame()).analyses[0]
    downgraded = [analysis.model_copy(update={"event_id": str(i), "publication": analysis.publication.model_copy(update={
        "was_research_target": True, "research_status": "quality_downgrade", "material_gate_passed": i != 0,
    })}) for i in range(7)]
    failed = [analysis.model_copy(update={"event_id": f"f{i}", "publication": analysis.publication.model_copy(update={
        "was_research_target": True, "research_status": "research_failed", "material_gate_passed": False,
    })}) for i in range(3)]
    summary = summarize_quality(downgraded + failed)
    assert summary["quality_downgraded"] == 7 and summary["research_failed"] == 3
    assert summary["material_gate_extensive"] == 1


def test_application_entry_keeps_40_to_10_budget_and_preserves_history(tmp_path):
    import hashlib
    from pathlib import Path
    from daily_intel.app.orchestrator import run_application
    from daily_intel.core.models import AnalysisDraft
    from daily_intel.core.settings import load_settings
    from daily_intel.core.ports import LLMResult
    from daily_intel.github.pipeline import GitRunResult
    from daily_intel.infrastructure.storage.sqlite import SQLiteIntelligenceRepository
    from daily_intel.intelligence.claims import covering_claim_checks
    from daily_intel.intelligence.pipeline import IntelligencePipeline
    from daily_intel.market.pipeline import MarketRunResult

    cfg = load_settings(Path(__file__).resolve().parents[1] / "config/settings.deepseek.yaml")
    cfg["paths"]["output_dir"] = str(tmp_path / "output")
    repository = SQLiteIntelligenceRepository(tmp_path / "app.db")
    quote_a = "Source records a reproducible technical experiment with explicit conditions and a complete description of its method."
    quote_b = "Independent replication remains necessary before production deployment, because the current benchmark uses a limited dataset."
    docs = [document().model_copy(update={
        "id": f"doc-{i}", "external_id": str(i), "content_hash": str(i),
        "title": hashlib.sha256(str(i).encode()).hexdigest(),
        "url": f"https://example.com/{i}", "canonical_url": f"https://example.com/{i}",
        "content": quote_a + "\n" + quote_b, "summary": quote_a,
    }) for i in range(45)]

    class BudgetLLM(FakeLLM):
        failed_event = None
        analysts = 0

        def generate(self, stage, system, user, schema):
            payload = json.loads(user)
            if stage == "analyst":
                result = super().generate(stage, system, user, schema)
                self.analysts += 1
                if self.failed_event is None:
                    self.failed_event = payload["event"]["id"]
                doc = payload["documents"][0]
                evidence = [e.model_copy(update={"document_id": doc["document_id"], "url": doc["url"], "quote": q}) for e, q in zip(result.value.evidence, [quote_a, quote_b])]
                result.value = result.value.model_copy(update={"evidence": evidence})
                return result
            if stage == "verifier":
                if payload["event"]["id"] == self.failed_event:
                    raise ModelResponseError("empty_response", model="actual-verifier")
                value = VerificationResult(verdict="pass", confidence_adjustment=0, notes="fixture",
                    supported_evidence_indexes=[0, 1], claim_checks=covering_claim_checks(AnalysisDraft.model_validate(payload["draft"])))
                return LLMResult(value, "actual-verifier", 10, 5)
            return super().generate(stage, system, user, schema)

    llm = BudgetLLM()
    pipeline = IntelligencePipeline(cfg, repository, llm)
    pipeline.collector.collect_sources = lambda now: (docs, [])
    pipeline.researcher.enricher = lambda d, timeout, limit: d
    previous = tmp_path / "output/2026-08-24/runs/previous/daily_digest.html"
    previous.parent.mkdir(parents=True)
    previous.write_bytes(b"preserved historical report")
    market = SimpleNamespace(run=lambda: MarketRunResult(pd.DataFrame(), {
        "market_date": "2026-08-24", "is_trading_day": True, "market_source_status": [],
    }, {}))
    github = SimpleNamespace(run=lambda now: GitRunResult([]))
    outputs = run_application(cfg, now=NOW, require_ai=True, repository=repository,
        intelligence_workflow=pipeline, market_workflow=market, github_workflow=github,
        experiment_id="remediation-fixture", run_name="remediation-fixture")
    report = outputs["html"]
    assert report.name == "daily_digest.html" and report.parent.parent.name == "runs"
    assert list(report.parent.iterdir()) == [report]
    assert list(report.parent.parent.parent.iterdir()) == [report.parent.parent]
    assert previous.read_bytes() == b"preserved historical report"
    assert llm.analysts == 10
    with sqlite3.connect(tmp_path / "app.db") as db:
        assert db.execute("SELECT count(*) FROM publication_ledger").fetchone()[0] == 40
        assert db.execute("SELECT count(*) FROM publication_ledger WHERE research_status='research_failed' AND publication_tier='extensive'").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM publication_ledger WHERE research_status='broad_only'").fetchone()[0] == 30
        assert db.execute("SELECT count(*) FROM publication_ledger WHERE research_status='deep_pass'").fetchone()[0] == 9
        assert db.execute("SELECT count(*) FROM research_audits WHERE stage='public_result'").fetchone()[0] == 40


def test_supported_label_cannot_bypass_spliced_quote_check():
    from test_intelligence_quality import _draft, _document, _verification
    from daily_intel.intelligence.quality import AnalysisQualityGate
    draft = _draft()
    verification = _verification(draft)
    verification.claim_checks[0].quote = "Exact source...technical detail."
    decision = AnalysisQualityGate(QualityPolicy()).evaluate(draft, verification, [_document()])
    assert not decision.deep
    assert decision.claims[0].status == ClaimStatus.UNVERIFIABLE
    assert "连续原文" in decision.claims[0].notes


def test_valid_claim_quotes_supply_evidence_when_indexes_are_omitted():
    from test_intelligence_quality import _draft, _document, _verification
    from daily_intel.intelligence.quality import AnalysisQualityGate
    draft = _draft()
    decision = AnalysisQualityGate(QualityPolicy()).evaluate(
        draft, _verification(draft, supported_evidence_indexes=[]), [_document()])
    assert decision.deep and len(decision.evidence) >= 2
    assert all(e.quote in _document().content for e in decision.evidence)


def test_primary_link_follows_supported_headline_source():
    from test_intelligence_quality import _draft, _document, _verification
    from daily_intel.intelligence.quality import AnalysisQualityGate
    doc = _document()
    other = doc.model_copy(update={"id": "other", "url": "https://example.com/other"})
    draft = _draft()
    draft.evidence[1] = draft.evidence[1].model_copy(update={"document_id": other.id, "url": other.url})
    verification = _verification(draft)
    verification.claim_checks[0].document_id = other.id
    decision = AnalysisQualityGate(QualityPolicy()).evaluate(draft, verification, [doc, other])
    assert decision.evidence[0].url == other.url


def test_cache_with_non_contiguous_claim_quote_is_rejected(tmp_path, monkeypatch):
    pipeline = _pipeline(tmp_path, FakeLLM(), monkeypatch)
    cached = pipeline.run(NOW, pd.DataFrame()).analyses[0]
    cached.quality.claim_audits[0].quote = "First...source."
    assert pipeline.researcher.prepare_cached(cached, [document()], True) is None


def test_sdk_retries_are_explicitly_disabled(monkeypatch):
    monkeypatch.setenv("REMEDIATION_SDK_TEST_KEY", "test-only")
    client = OpenAICompatibleLLM({"api_key_env": "REMEDIATION_SDK_TEST_KEY", "base_url": "https://example.invalid"})
    assert client._client.max_retries == 0
    client._client.close()


@pytest.mark.parametrize("stage", ["scout", "analyst", "verifier", "digest_brief", "git_brief"])
def test_requests_honor_explicit_output_token_limits(stage):
    client, calls = client_with_responses([response('{"answer": 42}')])
    client.config[stage] = {"model": "configured", "max_output_tokens": 32000}
    client.generate(stage, "system", "user", Answer)
    assert calls[0]["max_tokens"] == 32000
