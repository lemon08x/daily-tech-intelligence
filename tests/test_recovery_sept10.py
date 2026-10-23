import json

import pytest
import httpx
from openai import APITimeoutError

from daily_intel.core.models import ScoutBatch, ScoutItem
from daily_intel.core.ports import LLMResult
from daily_intel.infrastructure.llm.openai_compatible import ModelResponseError
from test_remediation_sept08 import Answer, client_with_responses, response
from test_intelligence_pipeline import FakeLLM, _pipeline, document, NOW
from daily_intel.intelligence.clustering import cluster_documents


@pytest.mark.parametrize("recover", [True, False])
def test_timeout_retries_once_and_records_request(tmp_path, monkeypatch, recover):
    timeout = APITimeoutError(request=httpx.Request("POST", "http://test/v1/chat/completions"))
    client, calls = client_with_responses([
        timeout, response('{"answer":42}') if recover else timeout,
    ])
    runner = _pipeline(tmp_path, client, monkeypatch).stages
    if recover:
        assert runner._generate('scout', 'system', 'user', Answer, 'event').value.answer == 42
    else:
        with pytest.raises(ModelResponseError) as error:
            runner._generate('scout', 'system', 'user', Answer, 'event')
        assert error.value.category == 'network_timeout'
        assert error.value.request_parameters['messages'][1]['content'] == 'user'
    assert len(calls) == 2
    assert runner.usage['usage_unknown'] is True


def test_truncation_retry_changes_budget_and_preserves_error_body(tmp_path, monkeypatch):
    client, calls = client_with_responses([
        response('{"answer":', 'length'), response('{"answer":42}')])
    client.config['verifier'] = {'model': 'configured', 'max_output_tokens': 131072,
                                 'retry_max_output_tokens': 262144}
    runner = _pipeline(tmp_path, client, monkeypatch).stages
    result = runner._generate('verifier', 'system', 'user', Answer, 'event')
    assert result.value.answer == 42
    assert [c['max_tokens'] for c in calls] == [131072, 262144]
    import sqlite3
    with sqlite3.connect(tmp_path / 'intel.db') as db:
        payload = json.loads(db.execute("select payload_json from research_audits where stage='verifier_attempt' order by id").fetchone()[0])
        diagnostic = json.loads(db.execute('select text from content_snapshots where hash=?',
                                          (payload['response_snapshot_hash'],)).fetchone()[0])
    assert diagnostic['response_content'] == '{"answer":'


def test_schema_retry_includes_validation_details():
    client, calls = client_with_responses([response('{"wrong":1}'), response('{"answer":42}')])
    with pytest.raises(ModelResponseError) as error:
        client.generate('scout', 'system', 'user', Answer)
    assert 'answer' in error.value.validation_errors
    client.retry_generate('scout', 'system', 'user', Answer, error.value)
    assert 'answer' in calls[1]['messages'][0]['content']
    assert calls[0]['messages'] != calls[1]['messages']


def test_scout_failed_middle_batch_preserves_other_batches(tmp_path, monkeypatch):
    runner = _pipeline(tmp_path, FakeLLM(), monkeypatch).stages
    doc = document()
    event = cluster_documents([doc], runner.settings['topics'], 72, 88)[0]
    pairs = [(event.model_copy(update={'id': str(n)}), [doc]) for n in range(6)]
    seen = []
    def generate(stage, system, user, schema, event_id):
        ids = [e['event_id'] for e in json.loads(user)['events']]
        seen.append(ids)
        if '2' in ids:
            raise ModelResponseError('schema_mismatch')
        return LLMResult(ScoutBatch(items=[ScoutItem(event_id=i, relevant=True, topic_id='compute',
            relevance=80, novelty=80, technical_depth=80, industry_impact=80, reason='ok') for i in ids]), 'scout')
    monkeypatch.setattr(runner, '_generate', generate)
    result = runner.scout(pairs, runner.settings['topics'], batch_size=2)
    assert [i.event_id for i in result.items] == ['0', '1', '3', '4', '5']
    assert ['4', '5'] in seen
    assert len(runner.scout_errors) == 1 and 'events=2' in runner.scout_errors[0]


def test_saved_analyst_resumes_only_identical_scope_and_input(tmp_path, monkeypatch):
    llm = FakeLLM()
    runner = _pipeline(tmp_path, llm, monkeypatch).stages
    runner.audit_context = {'cache_scope': 'one'}
    doc = document()
    event = cluster_documents([doc], runner.settings['topics'], 72, 88)[0]
    first, _ = runner.analyze(event, [doc])
    second, _ = runner.analyze(event, [doc])
    assert first == second and llm.calls.count('analyst') == 1
    runner.analyze(event, [doc.model_copy(update={'content': doc.content + ' changed'})])
    assert llm.calls.count('analyst') == 2
    runner.audit_context['cache_scope'] = 'two'
    runner.analyze(event, [doc])
    assert llm.calls.count('analyst') == 3
    runner.audit_context['force_analysis'] = True
    runner.analyze(event, [doc])
    assert llm.calls.count('analyst') == 4


def test_scout_recovers_omitted_events_without_losing_returned_items(tmp_path, monkeypatch):
    runner = _pipeline(tmp_path, FakeLLM(), monkeypatch).stages
    doc = document()
    event = cluster_documents([doc], runner.settings['topics'], 72, 88)[0]
    pairs = [(event.model_copy(update={'id': str(n)}), [doc]) for n in range(3)]
    seen = []
    def generate(stage, system, user, schema, event_id):
        ids = [e['event_id'] for e in json.loads(user)['events']]
        seen.append(ids)
        return LLMResult(ScoutBatch(items=[ScoutItem(event_id=ids[0], relevant=True,
            topic_id='compute', relevance=80, novelty=80, technical_depth=80,
            industry_impact=80, reason='ok')]), 'scout')
    monkeypatch.setattr(runner, '_generate', generate)
    result = runner.scout(pairs, runner.settings['topics'])
    assert [i.event_id for i in result.items] == ['0', '1', '2']
    assert seen == [['0', '1', '2'], ['1', '2'], ['2']]
    assert not runner.scout_errors
