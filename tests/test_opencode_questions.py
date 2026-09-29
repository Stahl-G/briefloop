"""Native question contracts: OpenCode v1.18.30 and v2.0.14 (Form API).

Fixtures mirror their tagged official OpenAPI and v2 question tool's toField;
no provider, native configuration, or model execution is involved.
"""
import copy
from urllib.parse import urlsplit, parse_qs

import pytest

from briefloop.backends.opencode_server import OpencodeServerClient, OpencodeError
from briefloop.opencode_harness import OpencodeHarness, _permission_rules
from briefloop.store import Store
from test_opencode_harness import FakeClient, until

QUESTIONS = [
    {'question': 'Which format?', 'header': 'Format', 'options': [
        {'label': 'PDF', 'description': 'Read only'}, {'label': 'Word', 'description': 'Editable'}], 'custom': False},
    {'question': 'Which topics?', 'header': 'Topics', 'options': [
        {'label': 'Solar', 'description': 'Panels'}, {'label': 'Storage', 'description': 'Batteries'}], 'multiple': True},
]
REQUEST = {'id': 'que_fixture', 'sessionID': 'ses_fake', 'questions': QUESTIONS,
           'tool': {'messageID': 'msg_native', 'callID': 'call_question'}}
ANSWERS = {'q0': {'answers': ['Word']}, 'q1': {'answers': ['Solar', 'Custom topic']}}


def test_v1_question_routes_scope_and_order(tmp_path):
    client = OpencodeServerClient.__new__(OpencodeServerClient); client.major = 1
    calls = []
    permission = [{'permission': 'question', 'action': 'allow', 'pattern': '*'}]
    def request(method, path, body=None):
        calls.append((method, path, body))
        route = urlsplit(path).path
        assert parse_qs(urlsplit(path).query) == {'directory': [str(tmp_path)]}
        if route == '/question':
            return [copy.deepcopy(REQUEST), {**REQUEST, 'id': 'que_other', 'sessionID': 'ses_other'}]
        if route == '/session/ses_fake':
            return {'id': 'ses_fake', 'permission': permission}
        return True
    client._request = request
    assert client.questions('ses_fake', directory=tmp_path) == [REQUEST]
    normalized = [{'id': 'q0'}, {'id': 'q1'}]
    assert client.reply_question('ses_fake', 'que_fixture', normalized, dict(reversed(list(ANSWERS.items()))), directory=tmp_path)
    assert calls[-1][2] == {'answers': [['Word'], ['Solar', 'Custom topic']]}
    client.reject_question('ses_fake', 'que_fixture', directory=tmp_path)
    assert urlsplit(calls[-1][1]).path == '/question/que_fixture/reject'
    client.set_permissions('ses_fake', permission, directory=tmp_path)
    assert calls[-2][0] == 'PATCH' and calls[-2][2] == {'permission': permission}


def test_v2_actual_form_contract_round_trip_and_preserved_rules(tmp_path):
    client = OpencodeServerClient.__new__(OpencodeServerClient); client.major = 2
    calls = []; permissions = []
    fields = [dict(key=f'q{i}', title=q['header'], description=q['question'],
                   type='multiselect' if q.get('multiple') else 'string', custom=q.get('custom', True),
                   options=[dict(value=o['label'], **o) for o in q['options']]) for i, q in enumerate(QUESTIONS)]
    form = dict(id='frm_question', sessionID='ses_fake', title='Questions', metadata={'kind': 'question'}, fields=fields)
    def request(method, path, body=None):
        calls.append((method, path, body))
        if path == '/api/session/ses_fake':
            if method == 'PATCH': permissions[:] = body['permissions']
            return {'data': {'id': 'ses_fake', 'location': {'directory': str(tmp_path)}, 'permissions': permissions[:]}}
        if path == '/api/session/ses_fake/form': return {'data': [form]}
        return None
    client._request = request
    rows = client.questions('ses_fake', directory=tmp_path)
    assert len(rows) == 1 and rows[0]['id'] == 'frm_question'
    assert not any('/form/' in path for _, path, _ in calls), 'list returns pending forms, not history'
    assert rows[0]['questions'][1]['multiple'] is True
    questions = [{'id': 'q0', 'multiSelect': False}, {'id': 'q1', 'multiSelect': True}]
    client.reply_question('ses_fake', 'frm_question', questions, ANSWERS, directory=tmp_path)
    assert calls[-1] == ('POST', '/api/session/ses_fake/form/frm_question/reply',
                         {'answer': {'q0': 'Word', 'q1': ['Solar', 'Custom topic']}})
    client.reject_question('ses_fake', 'frm_question', directory=tmp_path)
    assert calls[-1] == ('DELETE', '/api/session/ses_fake/form/frm_question', None)
    client.set_permissions('ses_fake', _permission_rules({'permission': 'read-only'}, False, tmp_path, interactive=True), directory=tmp_path)
    assert {'action': 'question', 'resource': '*', 'effect': 'allow'} in permissions
    assert {'action': 'shell', 'resource': '*', 'effect': 'deny'} in permissions
    assert {'action': 'webfetch', 'resource': '*', 'effect': 'deny'} in permissions
    assert not any('/question/' in path for _, path, _ in calls), 'v2.0.14 uses Form, not the old proposal'


class AskingClient(FakeClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mode = 'running'; self.replies = []; self.rejects = []; self.dead = False
        self.process = type('Process', (), {'poll': lambda proc: 1 if self.dead else None, 'pid': 4242})()
    def questions(self, session_id, *, directory=None):
        return [copy.deepcopy(REQUEST)] if self.prompts and not self.replies and not self.rejects else []
    def reply_question(self, session_id, request_id, questions, answers, *, directory=None):
        self.replies.append((session_id, request_id, copy.deepcopy(answers)))
        self.mode = 'complete'
        return True
    def reject_question(self, session_id, request_id, *, directory=None):
        self.rejects.append(request_id)
        return True


def asking(tmp_path, cls=AskingClient, internal=False, runtime=None):
    manager = OpencodeHarness(Store(tmp_path), cls)
    if internal:
        run = manager.start_internal('Work', runtime=runtime); sid = run.session_id
    else:
        sid = manager.create_session('Task', runtime=runtime)['id']; manager.send(sid, 'Work')
    return manager, sid


def test_interactive_question_deduplicates_validates_and_resumes(tmp_path):
    manager, sid = asking(tmp_path)
    try:
        until(lambda: any(r['status'] == 'pending' for r in manager.snapshot(sid)['requests']))
        request = manager.snapshot(sid)['requests'][0]; mid = manager.chat.session(sid)['turn_id']
        manager._poll_questions(manager.client, sid, mid, 'ses_fake', str(tmp_path))
        assert len(manager.snapshot(sid)['requests']) == 1
        assert request['data']['kind'] == 'question'
        assert request['data']['questions'][1]['multiSelect']
        assert not request['data']['questions'][0]['allowCustom']
        assert manager.client.created[0]['permission'][0]['action'] == 'allow'
        with pytest.raises(ValueError): manager.answer(sid, request['id'], {'q0': {'answers': ['Else']}})
        other = manager.create_session('Other')['id']
        with pytest.raises(ValueError): manager.answer(other, request['id'], ANSWERS)
        assert manager.chat.request(request['id'])['status'] == 'pending'
        manager.answer(sid, request['id'], ANSWERS)
        until(lambda: manager.chat.session(sid)['status'] == 'idle')
        assert manager.client.replies == [('ses_fake', 'que_fixture', ANSWERS)]
        assert manager.chat.request(request['id'])['status'] == 'answered'
        with pytest.raises(ValueError): manager.answer(sid, request['id'], ANSWERS)
        manager.send(sid, 'Next turn')
        until(lambda: len(manager.client.prompts) == 2)
        assert manager.client.last_permission[0]['action'] == 'allow'
    finally: manager.close()


@pytest.mark.parametrize('reason', ['cancel', 'disconnect', 'withdraw', 'close'])
def test_question_stale_cleanup(tmp_path, reason):
    manager, sid = asking(tmp_path)
    try:
        until(lambda: bool(manager.snapshot(sid)['requests']))
        request = manager.snapshot(sid)['requests'][0]
        if reason == 'cancel': manager.cancel(sid)
        elif reason == 'disconnect': manager.client.dead = True
        elif reason == 'close': manager.close()
        else:
            manager.client.replies.append(('external answer',))
            manager.client.mode = 'complete'
        until(lambda: manager.chat.request(request['id'])['status'] == 'expired')
        with pytest.raises(ValueError): manager.answer(sid, request['id'], ANSWERS)
        if reason == 'cancel': assert manager.client.rejects == ['que_fixture']
        assert not any(e['kind'] == 'input/answered' for e in manager.snapshot(sid)['events'])
    finally: manager.close()


def test_internal_questions_are_rejected_and_cannot_hang(tmp_path):
    manager, sid = asking(tmp_path, internal=True)
    try:
        until(lambda: manager.chat.session(sid)['status'] == 'failed')
        assert manager.client.created[0]['permission'][0]['action'] == 'deny'
        assert manager.client.rejects == ['que_fixture']
        assert manager.snapshot(sid)['requests'] == []
    finally: manager.close()


def test_unsupported_question_interface_fails_before_prompt(tmp_path):
    class Unsupported(AskingClient):
        def questions(self, session_id, *, directory=None): raise OpencodeError('unsupported', status=404)
    manager, sid = asking(tmp_path, cls=Unsupported)
    try:
        until(lambda: manager.chat.session(sid)['status'] == 'failed')
        assert manager.client.prompts == []
        assert any('不支持提问往返' in e['data'].get('message', '') for e in manager.snapshot(sid)['events'])
    finally: manager.close()


def test_uncertain_answer_is_not_retried(tmp_path):
    class Lost(AskingClient):
        def reply_question(self, *args, **kwargs):
            self.replies.append(('possibly delivered',))
            raise OpencodeError('connection lost')
    manager, sid = asking(tmp_path, cls=Lost)
    try:
        until(lambda: bool(manager.snapshot(sid)['requests']))
        request = manager.snapshot(sid)['requests'][0]
        with pytest.raises(OpencodeError): manager.answer(sid, request['id'], ANSWERS)
        assert manager.chat.request(request['id'])['status'] == 'expired'
        with pytest.raises(ValueError): manager.answer(sid, request['id'], ANSWERS)
        assert len(manager.client.replies) == 1
    finally: manager.close()
