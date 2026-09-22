"""The review contract is shared; host instructions and result hand-back are
backend-specific, and the native engine's submissions are admitted before the
run may end."""
import json
import queue

import pytest
from test_fact_check_contract import checked
from test_native_harness import EngineFixture, _wait_status

from briefloop import review
from briefloop.agent_prompts import system_prompt
from briefloop.native_harness import NativeHarness
from briefloop.store import Store


def _review_prompt(world, backend):
    store, brief = world['store'], world['brief']
    job = store.enqueue('review', {'version_id': brief['id'], 'agent_backend': backend,
                                   'runtime': {'model': 'deepseek/deepseek-v4-flash'}})
    folder = store.root / 'jobs' / job['id']
    captured = {}

    class Runtime:
        def execute(self, stage, prompt, target, **kwargs):
            captured['prompt'] = prompt
            packet = json.loads((target / 'packet' / 'index.json').read_text(encoding='utf-8'))
            output = {'fingerprint': packet['fingerprint'], 'version_id': brief['id'], 'status': 'incomplete',
                      'summary': '只核对了结构', 'coverage_scan_complete': False, 'claim_checks': [], 'findings': []}
            captured['output'] = output
            (target / 'review.json').write_text(json.dumps(output), encoding='utf-8')

    accepted = review.run_review(store, Runtime(), job, brief['id'], folder)
    return captured, folder, accepted


def test_hosts_get_their_own_instructions_over_one_review_contract(tmp_path):
    world = checked(tmp_path)
    external, folder, _ = _review_prompt(world, 'opencode')
    assert '只有read工具可用' in external['prompt']
    assert str(folder / 'packet' / 'index.json') in external['prompt']
    assert '最终回复一个符合' in external['prompt'] and 'submit_review' not in external['prompt']

    native, native_folder, _ = _review_prompt(checked(tmp_path / 'native'), 'briefloop-native')
    text = native['prompt']
    # Nothing the engine cannot do: no read-only-tool claim, no absolute paths,
    # no promise that figures are always attached, no JSON-in-the-reply rule.
    for stale in ('只有read工具可用', '原生read', '会作为原生图片附件', '最终回复一个符合', str(native_folder)):
        assert stale not in text, stale
    assert 'submit_review' in text and '视觉输入说明' in text
    # The review contract itself is the same for both hosts.
    for shared in ('response_checks', 'coverage_scan_complete', 'source_statements', 'unchecked_items'):
        assert shared in text and shared in external['prompt']


def test_check_review_runs_admission_without_saving(tmp_path):
    world = checked(tmp_path)
    store, brief = world['store'], world['brief']
    job = store.enqueue('review', {'version_id': brief['id']})
    folder = store.root / 'jobs' / job['id']
    fingerprint, files = review.build_packet(store, brief['id'], folder)
    identity = 'review_dryrun'
    with store.tx() as c:
        c.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',
                  (identity, brief['id'], job['id'], fingerprint, 'running',
                   json.dumps({'packet_path': str((folder / 'packet').relative_to(store.root)),
                               'files': files}),
                   None, '2026-09-16', '2026-09-16'))
    good = {'fingerprint': fingerprint, 'version_id': brief['id'], 'status': 'incomplete',
            'summary': 's', 'coverage_scan_complete': False, 'claim_checks': [], 'findings': []}
    review.check_review(store, identity, good)
    assert store.rows('SELECT result FROM reviews WHERE id=?', (identity,))[0]['result'] is None
    with pytest.raises(ValueError, match='未绑定本次正文'):
        review.check_review(store, identity, {**good, 'fingerprint': 'other'})
    with pytest.raises(ValueError, match='范围外'):
        review.check_review(store, identity, {**good, 'claim_checks': [
            {'claim_id': 'claim_not_in_packet', 'status': 'supported_for_scope', 'reason': 'r'}]})


def test_reviewer_system_prompt_is_layered():
    prompt = system_prompt('reviewer')
    text = prompt['text']
    assert text.index('## 授权与可信边界') < text.index('## 当前角色：独立只读 Reviewer') < text.index('## 后台执行模式')
    assert 'coding' not in text.lower()
    assert prompt == system_prompt('reviewer', 'background') and len(prompt['version']) == 16
    with pytest.raises(ValueError):
        system_prompt('writer')


class SubmittingEngine(EngineFixture):
    """Emits one submit_review admission request before ending the turn."""
    def call(self, method, params, timeout=None):
        if method != 'turn_start':
            return super().call(method, params, timeout)
        self.calls.append((method, params))
        sink = self.sinks[params['execution_id']]
        sink.put({'kind': 'submit', 'request_id': 'adm-1', 'review': {'status': 'complete'}})
        sink.put({'kind': 'end', 'status': 'completed', 'final_text': '{"status":"complete"}'})
        return {'started': True}


def test_harness_sends_the_layered_prompt_and_answers_admission(tmp_path, monkeypatch):
    engine = SubmittingEngine()
    store = Store(tmp_path)
    h = NativeHarness(store, engine)
    seen = []

    def reject(store_arg, review_id, value):
        seen.append((review_id, value))
        raise ValueError('完整审阅遗漏正文已使用主张，必须标为未完成')

    monkeypatch.setattr(review, 'check_review', reject)
    sid = h.create_session('review', {'model': 'fake/m1', 'review_root': str(tmp_path),
                                      'review_id': 'review_x'})['id']
    monkeypatch.setattr(h, '_visual_inputs', lambda config: [{'file': 'figures/f.png', 'sha256': 'a'}])
    snap = _wait_status(h, sid, h.send(sid, 'review')['id'], 'completed')

    created = next(p for name, p in engine.calls if name == 'session_create')
    assert created['system_prompt'] == system_prompt('reviewer')['text']
    assert created['admission'] == 'runner', 'structure is judged by the admission that saves the review'
    started = next(p for name, p in engine.calls if name == 'turn_start')
    assert started['require_submit'] is True and started['images'] == [{'file': 'figures/f.png', 'sha256': 'a'}]
    assert seen == [('review_x', {'status': 'complete'})]
    result = next(p for name, p in engine.calls if name == 'submit_result')
    assert result == {'session_id': sid, 'request_id': 'adm-1', 'ok': False,
                      'error': '完整审阅遗漏正文已使用主张，必须标为未完成'}
    bound = next(e['data'] for e in snap['events'] if e['kind'] == 'session/bound')
    assert bound['prompt_version'] == system_prompt('reviewer')['version']


def test_native_review_defaults_to_low_effort_unless_selected():
    from briefloop.native_harness import _thinking
    assert _thinking({}) == 'low'
    assert _thinking({'variant': 'high'}) == 'high'
    assert _thinking({'model_variant': ' MAX '}) == 'max'
    with pytest.raises(ValueError, match='不支持'):
        _thinking({'variant': 'not-a-level'})
