"""The review contract is shared; host instructions and result hand-back are
backend-specific, and the native engine's submissions are admitted before the
run may end."""
import json

from test_native_harness import EngineFixture, _wait_status

from briefloop import review
from briefloop.agent_prompts import system_prompt
from briefloop.native_harness import NativeHarness
from briefloop.store import Store


def _review_prompt(world, backend, review_mode="standard"):
    store, brief = world['store'], world['brief']
    job = store.enqueue('review', {'version_id': brief['id'], 'agent_backend': backend, 'review_mode': review_mode,
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
