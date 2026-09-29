"""Cross-host answers stay user choices, never permission grants."""
import pytest
from briefloop.user_input import normalize_questions, validate_answers
from briefloop.store import Store
from briefloop.harness import HarnessManager
from briefloop.bridge_harness import BridgeHarness
from test_harness import RPC, until
from test_bridge_harness import BridgeFixture


QUESTIONS = [
    {'id': 'scope', 'question': '关注哪些范围？', 'header': '范围', 'multiSelect': True,
     'allowCustom': False, 'options': [{'label': '芯片'}, {'label': '数据中心'}]},
    {'id': 'period', 'question': '覆盖哪个期间？', 'options': [{'label': '本周'}]},
]
ANSWERS = {'scope': {'answers': ['芯片', '数据中心']}, 'period': {'answers': ['2026 年 9 月']}}


def test_answer_contract_keeps_multiple_custom_and_rejects_incomplete_answers():
    assert validate_answers(QUESTIONS, ANSWERS) == ANSWERS
    for answers in ({'scope': ANSWERS['scope']}, {**ANSWERS, 'unknown': ['x']},
                    {**ANSWERS, 'scope': ['未列出的选项']}, {**ANSWERS, 'period': ['本周', '下周']}):
        with pytest.raises(ValueError):
            validate_answers(QUESTIONS, answers)
    # Text-only questions cannot become impossible to answer.
    assert normalize_questions([{'id': 'text', 'question': '补充信息', 'isOther': False}])[0]['allowCustom']
    editor=[{'id':'edit','question':'修改正文','inputType':'editor','prefill':'  foo()\n'}]
    assert normalize_questions(editor)[0]['prefill']=='  foo()\n'
    for text in ('  foo()\n',''):
        assert validate_answers(editor,{'edit':[text]})=={'edit':{'answers':[text]}}


def test_codex_questions_keep_choice_shape_and_cannot_be_answered_twice(tmp_path):
    h = HarnessManager(Store(tmp_path), RPC)
    sid = h.create_session()['id']
    try:
        h.send(sid, '合成问题协议验收')
        until(lambda: h.snapshot(sid)['session']['turn_id'] == 'turn1')
        h.client.server_requests.put({'id': 42, 'method': 'item/tool/requestUserInput',
            'params': {'threadId': 't1', 'turnId': 'turn1', 'questions': QUESTIONS}})
        until(lambda: len(h.snapshot(sid)['requests']) == 1)
        request = h.snapshot(sid)['requests'][0]
        assert request['data']['kind'] == 'question'
        assert request['data']['questions'][0]['multiSelect'] is True
        h.answer(sid, request['id'], ANSWERS)
        assert h.client.calls[-1] == ('answer', {'id': 42, 'result': {'answers': ANSWERS}})
        with pytest.raises(ValueError):
            h.answer(sid, request['id'], ANSWERS)
    finally:
        h.close()


@pytest.mark.parametrize('finish', ['answer', 'cancel', 'disconnect', 'uncertain'])
def test_bridge_question_reply_and_lifecycle(tmp_path, finish):
    class InputBridge(BridgeFixture):
        def __init__(self):
            super().__init__()
            self.answers = []

        def call(self, method, params, timeout=None):
            if method == 'start':
                self.starts.append(params)
                self.sinks[params['execution_id']].put({'kind': 'question', 'type': 'user_input',
                    'request_id': 'ask1', 'questions': QUESTIONS})
                return {}
            if method == 'answer':
                self.answers.append(params)
                if finish == 'uncertain':raise TimeoutError('Synthetic lost reply receipt')
            if method in ('answer', 'cancel'):
                sink = self.sinks.get(params['execution_id'])
                if sink:
                    sink.put({'kind': 'end', 'status': 'completed' if method == 'answer' else 'cancelled'})
            return {}

    bridge = InputBridge()
    h = BridgeHarness(Store(tmp_path), bridge, 'claude')
    sid = h.create_session()['id']
    try:
        h.send(sid, '合成问题协议验收', message_id='ask-turn')
        until(lambda: bool(h.snapshot(sid)['requests']))
        request = h.snapshot(sid)['requests'][0]
        assert request['data']['kind'] == 'question'
        assert 'native_options' not in request['data']
        assert bridge.starts[0]['host_options'] == {'mode': 'auto'}
        if finish == 'answer':
            h.answer(sid, request['id'], ANSWERS)
            assert bridge.answers == [{'execution_id': 'ask-turn', 'request_id': 'ask1', 'answers': ANSWERS}]
            until(lambda: h.snapshot(sid)['session']['status'] == 'idle')
            assert h.chat.request(request['id'])['status'] == 'answered'
        elif finish=='uncertain':
            with pytest.raises(TimeoutError):h.answer(sid,request['id'],ANSWERS)
            until(lambda:h.chat.request(request['id'])['status']=='expired')
            assert len(bridge.answers)==1
            assert sid in h._cancel_requested
        else:
            if finish == 'cancel':
                h.cancel(sid)
            else:
                bridge.sinks['ask-turn'].put({'kind': 'end', 'status': 'failed'})
            until(lambda: h.chat.request(request['id'])['status'] == 'expired')
            assert not bridge.answers
        with pytest.raises(ValueError):
            h.answer(sid, request['id'], ANSWERS)
    finally:
        h.close()


@pytest.mark.parametrize('uncertain',[False,True])
def test_codex_answer_receipt_wins_completion_race_and_uncertainty_stops_turn(tmp_path,uncertain):
    h=HarnessManager(Store(tmp_path),RPC)
    sid=h.create_session()['id']
    try:
        h.send(sid,'合成问题协议验收')
        until(lambda:h.snapshot(sid)['session']['turn_id']=='turn1')
        rid=h.chat.add_request(sid,42,{'kind':'question','questions':QUESTIONS,'turnId':'turn1'})
        def reply(_id,_result):
            if uncertain:raise TimeoutError('Synthetic lost reply receipt')
            h._finish(sid,'turn1','completed')
        h.client.answer=reply
        if uncertain:
            with pytest.raises(TimeoutError):h.answer(sid,rid,ANSWERS)
            assert h.chat.request(rid)['status']=='expired'
            assert h.client.calls[-1][0]=='turn/interrupt'
        else:
            h.answer(sid,rid,ANSWERS)
            assert h.chat.request(rid)['status']=='answered'
            assert h.snapshot(sid)['events'][-1]['kind']=='input/answered'
    finally:h.close()


def test_recovery_expires_inflight_answer_without_replaying_it(tmp_path):
    from briefloop.chat_store import ChatStore
    chat = ChatStore(Store(tmp_path))
    sid = chat.create('恢复', {}, tmp_path)['id']
    rid = chat.add_request(sid, 'native-q', {'kind': 'question', 'questions': QUESTIONS})
    chat.request_status(rid, 'answering')
    assert chat.session(sid)['busy']
    chat.recover_stale()
    assert chat.request(rid)['status'] == 'expired'
    assert not chat.session(sid)['busy']
