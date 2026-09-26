"""Public progress cannot corrupt a completed read-only structured reply."""
import json

import pytest

from briefloop.interactive_runtime import InteractiveRuntime
from briefloop.store import Store
from test_interactive_runtime import FakeHarness

pytestmark = pytest.mark.real_review_capabilities


class ReplyHarness(FakeHarness):
    def __init__(self, replies):
        super().__init__()
        self.replies = replies

    def start_internal(self, text, **kwargs):
        result = super().start_internal(text, **kwargs)
        snapshot = self.sessions[kwargs['session_id']]
        snapshot['messages'][0]['status'] = 'completed'
        # A valid older turn must never rescue the current final response.
        snapshot['messages'].append({'id':'old-final', 'role':'assistant',
            'text':'{"from_old_turn":true}', 'turn_id':'older-turn', 'status':'completed'})
        for index, reply in enumerate(self.replies):
            snapshot['messages'].append({'id':f'reply-{index}', 'role':'assistant', 'text':reply,
                'turn_id':kwargs['message_id'], 'status':'completed'})
        return result


def execute_reply(tmp_path, replies):
    store = Store(tmp_path/'workspace')
    job = store.enqueue('review', {'agent_backend':'codex', 'review_mode':'standard',
                                  'runtime':{'model':'synthetic-no-call'}})
    job.update(readonly_output='review.json', input_source_ids=[])
    folder = store.root/'jobs'/job['id']
    runtime = InteractiveRuntime(store, ReplyHarness(replies))
    return runtime, job, folder


def test_final_json_is_admitted_without_discarding_public_progress(tmp_path):
    replies = ['正在核对原文。', '```json\n{"synthetic_result":"complete"}\n```']
    runtime, job, folder = execute_reply(tmp_path, replies)
    assert runtime.execute(job, 'Synthetic contract', folder)['returncode'] == 0
    assert json.loads((folder/'review.json').read_text()) == {'synthetic_result':'complete'}
    assert (folder/'last-message.txt').read_text() == '\n\n'.join(replies)
    assert runtime.harness.starts[0][1]['runtime']['permission'] == 'read-only'


@pytest.mark.parametrize('broken_final', ['{"truncated":', '```json\n{"looks_complete":true}'])
def test_broken_final_does_not_fall_back_to_an_earlier_json(tmp_path, broken_final):
    replies = ['{"earlier_in_current_turn":true}', broken_final]
    runtime, job, folder = execute_reply(tmp_path, replies)
    with pytest.raises(json.JSONDecodeError):
        runtime.execute(job, 'Synthetic contract', folder)
    assert not (folder/'review.json').exists()
    assert (folder/'last-message.txt').read_text() == '\n\n'.join(replies)
