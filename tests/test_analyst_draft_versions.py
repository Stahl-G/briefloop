"""Same-content preflight and admission, through Native and the real CLI."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from briefloop import analyst
from briefloop.chat_store import ChatStore
from briefloop.native_roles import run_tool
from test_native_analyst import setup, draft, saved_revision


def writer(tmp_path):
    store, run, source, inputs = setup(tmp_path)
    from test_native_orchestrator import contract
    store.set_meta('reader_contract:' + run['id'], contract(store, run['id']))
    pack = analyst.packet(store, run['id'], store.root/'writer', **inputs)
    config = {'native_role': 'analyst', 'run_id': run['id'], 'packet_root': str(pack['root']),
              'result_file': str(pack['root'].parent/'draft.json'), 'attempt_id': 'attempt-a'}
    return store, run, source, config


def check(store, config, revision):
    result = run_tool(store, config, 'check_draft', {'revision': revision})
    assert result['ok'], result
    return json.loads(result['content'][0]['text'])


def test_metadata_edit_needs_new_check_and_replay_is_idempotent(tmp_path):
    store, run, source, config = writer(tmp_path)
    first = saved_revision(store, config, draft(source['id']))
    assert check(store, config, first)['diagnostics']['numbers']['total'] == 0
    value = {'base_revision': first, 'number_bindings': [{'label': '收入增速', 'value': 20, 'unit': '%',
        'source_id': source['id'], 'locator': 'line 1', 'source_excerpt': '2025年收入1200万元，同比增长20%。',
        'report_quote': '收入增长20%，下一步观察毛利能否同步改善。', 'number_text': '20%'}]}
    second = saved_revision(store, config, value)
    assert first != second
    assert not run_tool(store, config, 'submit_draft', {'revision': first})['ok']
    assert not run_tool(store, config, 'submit_draft', {'revision': second})['ok']
    assert check(store, config, second)['diagnostics']['numbers']['checked'] == 1
    assert check(store, config, second)['cached']
    assert run_tool(store, config, 'submit_draft', {'revision': second})['ok']
    before = Path(config['result_file']).read_bytes()
    assert run_tool(store, config, 'submit_draft', {'revision': second})['ok']
    assert Path(config['result_file']).read_bytes() == before
    saved = analyst.validate_draft(store, config, json.loads(before))
    assert saved['reader_contract'] == store.meta('reader_contract:' + run['id'])
    assert not run_tool(store, {**config, 'attempt_id': 'attempt-b'}, 'submit_draft', {'revision': second})['ok']


def test_changed_sections_or_sources_cannot_use_a_checked_revision(tmp_path):
    store, run, source, config = writer(tmp_path)
    def section(text):
        return run_tool(store, config, 'save_draft_section', {'section_id': 'one',
                        'content': draft(source['id'], text)['editor_document']['content']})
    assert section('最初稿')['ok']
    first = saved_revision(store, config, {'title': '报告', 'section_ids': ['one']})
    check(store, config, first)
    assert section('局部修订稿')['ok']
    assert not run_tool(store, config, 'submit_draft', {'revision': first})['ok']
    second = saved_revision(store, config, {'base_revision': first, 'section_ids': ['one']})
    check(store, config, second)
    (store.root/source['path']).write_text('来源被修改')
    assert not run_tool(store, config, 'submit_draft', {'revision': second})['ok']
    assert not Path(config['result_file']).exists()


def test_cli_preflight_checks_frozen_contract_and_submit_checks_file_version(tmp_path):
    store, run, source, config = writer(tmp_path)
    path = Path(config['result_file'])
    chat = ChatStore(store)
    session = chat.create('writer', {'backend': 'pi'}, path.parent)
    message = chat.message(session['id'], 'write', status='delivered')
    (path.parent/'conversation.json').write_text(json.dumps({'session_id': session['id'],
        'message_id': message['id'], 'job_id': 'job_writer'}))
    def cli(name, *extra):
        result = subprocess.run([sys.executable, '-m', 'briefloop', 'tool', '--workspace', str(store.root),
                    name, '--run', run['id'], '--file', str(path), *extra], capture_output=True, text=True)
        return result.returncode, json.loads(result.stdout)
    value = {**draft(source['id']), 'reader_contract': {'clauses': []}}
    path.write_text(json.dumps(value))
    code, rejected = cli('check-draft')
    assert code == 1 and 'reader_contract' in str(rejected)
    value.pop('reader_contract')
    path.write_text(json.dumps(value))
    code, checked = cli('check-draft')
    assert code == 0 and checked['scope'] == 'writer_packet'
    value['gaps'] = ['新增未核实事项']
    path.write_text(json.dumps(value))
    assert cli('submit-draft', '--revision', checked['revision'])[0] == 1
    code, latest = cli('check-draft')
    assert code == 0
    assert cli('submit-draft', '--revision', latest['revision'])[0] == 0
    assert json.loads(path.read_text())['gaps'] == value['gaps']
    chat.patch_message(message['id'], status='cancelled')
    assert cli('submit-draft', '--revision', latest['revision'])[0] == 1


def test_below_target_is_advisory_but_actual_errors_remain_visible(tmp_path):
    from briefloop.draft_checks import inspect_draft
    text = '文' * 2036
    result = inspect_draft({'title': '报告', 'markdown': text}, {'target_words': 2200, 'max_words': 2500})
    assert result['status'] == 'checks_completed'
    assert result['notes'][0]['code'] == 'below_target'
    result = inspect_draft({'title': '报告', 'markdown': text}, {'target_words': 1500, 'max_words': 2000})
    assert result['status'] == 'needs_attention'
    assert result['warnings'][0]['code'] == 'over_limit'


def test_writer_action_separates_unsupported_units_from_broken_bindings(tmp_path):
    store, run, source, config = writer(tmp_path)
    binding = {'label': '收入增速', 'value': 20, 'unit': '自定义口径',
        'source_id': source['id'], 'locator': 'line 1', 'source_excerpt': store.source_text(source['id']),
        'report_quote': '收入增长20%', 'number_text': '20%'}
    first = saved_revision(store, config, {**draft(source['id']), 'number_bindings': [binding]})
    checked = check(store, config, first)
    assert checked['diagnostics']['status'] == 'needs_attention'
    assert checked['writer_action']['next_operation'] == 'submit_draft'
    assert checked['writer_action']['unchecked_number_count'] == 1
    assert checked['writer_action']['review_status'] == 'not_reviewed'
    assert check(store, config, first)['writer_action'] == checked['writer_action']
    assert run_tool(store, config, 'submit_draft', {'revision': first})['ok']
    second = saved_revision(store, config, {'base_revision': first,
        'number_bindings': [{**binding, 'unit': '%', 'report_quote': '没有这句'}]})
    checked = check(store, config, second)
    assert checked['writer_action']['next_operation'] == 'repair_then_check'
    assert checked['writer_action']['number_binding_indexes'] == [0]
    # The diagnostic advice does not become a new gate on editable work drafts.
    assert run_tool(store, config, 'submit_draft', {'revision': second})['ok']


def test_saved_work_can_be_read_after_context_loss_without_new_permission(tmp_path):
    store, run, source, config = writer(tmp_path)
    def read(**args):
        value=run_tool(store, config, 'read_draft', args)
        assert value['ok'], value
        return json.loads(value['content'][0]['text'])
    assert read()['revision'] is None
    section={'section_id':'one','content':draft(source['id'])['editor_document']['content']}
    assert run_tool(store, config, 'save_draft_section', section)['ok']
    assert read()['section_ids']==['one']
    assert read(field='body',section_id='one')['items'][0]['content'][0]['text'].startswith('收入')
    revision=saved_revision(store,config,{'title':'报告','section_ids':['one'],'gaps':['不能把预测当事实']})
    check(store,config,revision)
    assert read()['checked'] and read()['revision']==revision
    assert read(field='gaps')['items']==['不能把预测当事实']
    section['content']=draft(source['id'],'修改后')['editor_document']['content']
    assert run_tool(store,config,'save_draft_section',section)['ok']
    assert read()['sections_changed_since_save'] and not read()['checked']
    assert not run_tool(store,config,'read_draft',{'field':'body','section_id':'../secret'})['ok']
    assert read(field='body',offset=100)['items']==[]


@pytest.mark.skipif(sys.platform != 'win32', reason='Real Windows long-path file I/O')
def test_windows_long_revision_paths_keep_saved_checked_and_submitted_drafts(tmp_path):
    from briefloop import analyst_drafts
    # The candidate directory is 191 characters; its 64-hex revision plus the
    # atomic-write suffix reaches 265, even though the writer packet fits.
    parent=tmp_path/('w'*max(1,142-len(str(tmp_path))-1))
    store, run, source, config=writer(parent)
    logical_root=analyst._sections_file(store,config).with_suffix('')
    assert len(str(logical_root/('a'*64+'.json.tmp')))>=265
    first=saved_revision(store,config,draft(source['id']))
    assert len(first)==64
    check(store,config,first)
    # Existing on-disk names and full revision identities stay compatible.
    disk_root=Path('\\\\?\\'+str(logical_root))
    old_bytes=(disk_root/(first+'.json')).read_bytes()
    assert analyst_drafts._hash(json.loads(old_bytes))==first
    second=saved_revision(store,config,{'base_revision':first,'gaps':['Keep this unresolved question']})
    assert second!=first and (disk_root/(first+'.json')).read_bytes()==old_bytes
    assert not run_tool(store,config,'submit_draft',{'revision':first})['ok']
    check(store,config,second)
    overview=run_tool(store,config,'read_draft',{})
    assert overview['ok'] and json.loads(overview['content'][0]['text'])['checked']
    assert run_tool(store,config,'submit_draft',{'revision':second})['ok']
    saved=Path(config['result_file']).read_bytes()
    assert json.loads(saved)['gaps']==['Keep this unresolved question']
    assert not list(disk_root.glob('*.tmp'))
    candidate=disk_root/(second+'.json')
    changed=json.loads(candidate.read_bytes());changed['draft']['title']='Tampered title'
    candidate.write_text(json.dumps(changed),encoding='utf-8')
    rejected=run_tool(store,config,'submit_draft',{'revision':second})
    assert not rejected['ok'] and '稿件版本内容' in rejected['error']
    assert Path(config['result_file']).read_bytes()==saved
