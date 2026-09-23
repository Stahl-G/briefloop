"""Codex generation sees the field rules before submitting workspace actions."""
import json

import pytest
from pydantic import ValidationError

from briefloop.chat_tools import workspace_action
from briefloop.evidence import EvidenceInput
from briefloop.reconciliation import ReconciliationError
from briefloop.research_plan import freeze
from briefloop.runtime import generation_prompt
from briefloop.store import Store


def setup_run(tmp_path):
    store = Store(tmp_path / 'workspace')
    source = store.add_source('Synthetic', 'An observation, not an established cause.')
    run = store.create_run({'title': 'Synthetic', 'objective': 'Summarize the supplied observation.',
                            'allow_web': False, 'extent': 'quick', 'research_tier': 'quick'}, [source['id']],
                           research_protocol='quality_v1')
    freeze(store, run['id'])
    return store, source, run


def test_codex_generation_prompt_exposes_discoverable_field_rules(tmp_path):
    store, source, run = setup_run(tmp_path)
    folder = tmp_path / 'task'
    folder.mkdir()
    prompt = generation_prompt(store, run, folder, backend='codex')
    capabilities = workspace_action(store, {'action': 'capabilities'})
    categories = capabilities['schemas']['evidence_span.evidence']['properties']['category']['enum']
    assert 'category=' + '|'.join(categories) in prompt
    assert json.dumps({'action': 'capabilities'}, ensure_ascii=False) in prompt
    assert 'workspace-action --request REQUEST_JSON' in prompt
    question_rule = capabilities['reconciliation_save.open_questions']
    assert 'question' in question_rule and '非空' in question_rule
    assert '{"question":' in question_rule
    assert question_rule in prompt


def test_discovered_rules_match_admission_without_changing_saved_questions(tmp_path):
    store, source, run = setup_run(tmp_path)
    capabilities = workspace_action(store, {'action': 'capabilities'})
    assert 'question' in capabilities['reconciliation_save.open_questions']
    example = {'source_id': source['id'], 'locator': {'kind': 'text', 'start_line': 1, 'end_line': 1}}
    categories = capabilities['schemas']['evidence_span.evidence']['properties']['category']['enum']
    for category in categories:
        assert EvidenceInput.model_validate({**example, 'category': category}).category == category
    with pytest.raises(ValidationError):
        EvidenceInput.model_validate({**example, 'category': 'observation'})
    request = {'action': 'reconciliation_save', 'run_id': run['id'],
               'reconciliation': {'status': 'partial', 'examined_claim_ids': [], 'unexamined_claim_ids': []}}
    for question in ('What caused it?', {'text': 'What caused it?'}, {'question': '   '}):
        with pytest.raises(ReconciliationError, match='question'):
            workspace_action(store, {**request, 'reconciliation': {**request['reconciliation'], 'open_questions': [question]}})
    questions = [{'question': 'What caused it?', 'note': 'No cause has been established.'}]
    request['reconciliation']['open_questions'] = questions
    saved = workspace_action(store, request)
    assert saved['open_questions'] == questions
    assert workspace_action(store, request)['id'] == saved['id']
