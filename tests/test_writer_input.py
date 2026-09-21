"""Writing input uses the real candidate/version/source admission path."""
import json
import pytest

from briefloop import analyst_drafts as drafts, writer_input as writer
from briefloop.document_model import source_ids
from test_analyst_draft_versions import writer as setup_writer


def test_article_is_converted_with_table_citations_and_replay(tmp_path):
    store, run, source, config = setup_writer(tmp_path)
    text = f'# 要点\n\n**收入**增长20%。[@{source["id"]}]\n\n| 指标 | 变化 |\n|---|---|\n| 收入 | 20% |'
    saved = writer.write_report(store, config, {'title': '报告', 'markdown': text})
    candidate = drafts._candidate(store, config, {'revision': saved['revision']})
    doc = candidate['draft']['editor_document']
    assert doc['content'][-1]['type'] == 'table'
    assert source_ids(doc) == [source['id']]
    assert writer.write_report(store, config, {'title': '报告', 'markdown': text})['revision'] == saved['revision']
    drafts.check(store, config, {'revision': saved['revision']})
    drafts.submit(store, config, {'revision': saved['revision']})
    assert json.loads(__import__('pathlib').Path(config['result_file']).read_text())['editor_document'] == doc


def test_lossy_input_and_out_of_scope_source_preserve_original(tmp_path):
    store, run, source, config = setup_writer(tmp_path)
    first = writer.write_report(store, config, {'title': '报告', 'markdown': '已有内容'})
    for text in ('<table><tr><td>内容</td></tr></table>', '![图](https://example.org/image.png)',
                 '| A | B |\n|---|---|\n|1|2|3|', '错误[@src_not_registered]'):
        with pytest.raises(ValueError): writer.write_report(store, config, {'title': '报告', 'markdown': text})
    assert drafts._read(drafts._root(store, config)/'current.json')['revision'] == first['revision']
    assert len(list((drafts._root(store, config)/'writer-inputs').glob('*.json'))) == 5


def test_sections_batch_atomic_and_hash_protected(tmp_path):
    store, run, source, config = setup_writer(tmp_path)
    from briefloop.analyst import _sections_file
    first = writer.write_sections(store, config, {'sections': [{'section_id': 'one', 'markdown': '第一节'}]})
    before = _sections_file(store, config).read_bytes()
    with pytest.raises(ValueError):
        writer.write_sections(store, config, {'sections': [{'section_id': 'two', 'markdown': '第二节'},
            {'section_id': 'bad', 'markdown': '<b>不支持</b>'}]})
    assert _sections_file(store, config).read_bytes() == before
    saved = writer.assemble_report(store, config, {'title': '报告', 'section_ids': ['one']})
    drafts.check(store, config, {'revision': saved['revision']})
    with pytest.raises(ValueError):
        writer.write_sections(store, config, {'sections': [{'section_id': 'one', 'markdown': '改稿'}]})
    writer.write_sections(store, config, {'sections': [{'section_id': 'one', 'markdown': '改稿',
        'expected_hash': first['sections'][0]['hash']}]})
    with pytest.raises(ValueError): drafts.submit(store, config, {'revision': saved['revision']})
    newer = writer.assemble_report(store, config, {'title': '报告', 'section_ids': ['one'], 'base_revision': saved['revision']})
    assert newer['revision'] != saved['revision']


def test_markup_literal_code_and_escaped_pipe():
    assert writer.compile_markdown('```html\n<b>示例</b>\n```')['content'][0]['type'] == 'codeBlock'
    assert writer.compile_markdown('|A|B|\n|---|---|\n|a\\|b|c|')['content'][0]['type'] == 'table'


def test_evidence_patch_is_small_and_invalidates_check(tmp_path):
    store, run, source, config = setup_writer(tmp_path)
    first = writer.write_report(store, config, {'title': '报告', 'markdown': f'收入增长20%。[@{source["id"]}]'})
    drafts.check(store, config, {'revision': first['revision']})
    value = {'source_id': source['id'], 'locator': 'line 1', 'excerpt': '2025年收入1200万元，同比增长20%。'}
    updated = writer.update_draft_evidence(store, config, {'base_revision': first['revision'],
        'field': 'citations', 'changes': [{'value': value}]})
    before = drafts._candidate(store, config, {'revision': updated['revision']})['draft']
    assert before['markdown'] == f'收入增长20%。[@{source["id"]}]'
    with pytest.raises(ValueError): drafts.submit(store, config, {'revision': updated['revision']})
    read = drafts.read_saved(store, config, {'field': 'citations'})
    assert read['record_keys'] == updated['record_keys']
    value['locator'] = 'line 1-2'
    newer = writer.update_draft_evidence(store, config, {'base_revision': updated['revision'],
        'field': 'citations', 'changes': [{'record_key': read['record_keys'][0], 'value': value}]})
    with pytest.raises(ValueError): writer.update_draft_evidence(store, config, {'base_revision': updated['revision'],
        'field': 'citations', 'changes': [{'value': value}]})
    assert newer['revision'] != updated['revision']


def test_text_and_block_repairs_preserve_other_rich_content(tmp_path):
    store, run, source, config = setup_writer(tmp_path)
    first = writer.write_report(store, config, {'title': '报告', 'markdown': f'**收入**增长20%。[@{source["id"]}]\n\n下一步观察。'})
    patched = writer.patch_report_text(store, config, {'base_revision': first['revision'],
        'replacements': [{'old_text': '下一步观察。', 'new_text': '下一步观察毛利。'}]})
    read = drafts.read_saved(store, config, {'field': 'body'})
    kept = read['items'][0]
    replaced = writer.replace_report_blocks(store, config, {'base_revision': patched['revision'],
        'block_keys': [read['block_keys'][1]], 'markdown': '| 指标 | 变化 |\n|---|---|\n|收入|20%|'})
    doc = drafts._candidate(store, config, {'revision': replaced['revision']})['draft']['editor_document']
    assert doc['content'][0] == kept
    assert doc['content'][1]['type'] == 'table'
    doc['content'][1]['content'][0]['content'][0]['attrs'] = {'backgroundColor': '#ffffff'}
    styled = drafts.save(store, config, {'base_revision': replaced['revision'], 'editor_document': doc})
    read = drafts.read_saved(store, config, {'field': 'body'})
    with pytest.raises(writer.WritingError, match='高级排版'):
        writer.replace_report_blocks(store, config, {'base_revision': styled['revision'],
            'block_keys': [read['block_keys'][1]], 'markdown': '替换表格'})


def test_native_and_cli_use_same_frozen_protocol(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    from briefloop.chat_store import ChatStore
    from briefloop.native_roles import run_tool, runner_tool_specs
    store, run, source, config = setup_writer(tmp_path)
    task = Path(config['packet_root'])/'input.json'
    payload = json.loads(task.read_text());payload['writer_input_protocol'] = writer.PROTOCOL
    task.write_text(json.dumps(payload))
    names = {t['name'] for t in runner_tool_specs('analyst', config=config)}
    assert 'write_report' in names and 'save_draft' not in names and 'save_draft_section' not in names
    chat = ChatStore(store)
    folder = Path(config['result_file']).parent
    session = chat.create('writer', {'backend':'pi'}, folder)
    message = chat.message(session['id'], 'write', status='delivered')
    config['attempt_id'] = message['id']
    (folder/'conversation.json').write_text(json.dumps({'session_id':session['id'], 'message_id':message['id']}))
    body = f'收入增长20%。[@{source["id"]}]'
    result = run_tool(store,config,'write_report',{'title':'报告','markdown':body})
    assert result['ok'], result
    revision = json.loads(result['content'][0]['text'])['revision']
    (folder/'article.md').write_text(body)
    def cli(operation,*args):
        proc = subprocess.run([sys.executable,'-m','briefloop','tool','--workspace',str(store.root),
            'writer','--run',run['id'],'--draft-file',config['result_file'],'--operation',operation,*args],capture_output=True,text=True)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return json.loads(proc.stdout)
    replay = cli('write_report','--file',str(folder/'article.md'),'--title','报告')
    assert replay['revision'] == revision
    assert cli('check_draft','--revision',revision)['revision'] == revision
    assert cli('submit_draft','--revision',revision)['revision'] == revision
    assert Path(config['result_file']).is_file()


def test_protocol_packet_contains_only_new_format_contract(tmp_path):
    from briefloop import analyst
    from test_native_analyst import setup
    store, run, source, inputs = setup(tmp_path)
    packet = analyst.packet(store, run['id'], store.root/'new-writer', writer_protocol=writer.PROTOCOL, **inputs)
    root = packet['root']
    assert json.loads((root/'input.json').read_text())['writer_input_protocol'] == writer.PROTOCOL
    schema = json.loads((root/'draft.schema.json').read_text())
    assert 'write_report' in schema['tools'] and 'save_draft' not in schema['tools']
    assert '|' in json.loads((root/'document-guide.json').read_text())['table_example']


def test_revision_starts_from_original_without_retyping(tmp_path):
    from briefloop import analyst
    from briefloop.native_roles import run_tool
    from test_native_analyst import setup, draft
    store, run, source, inputs = setup(tmp_path)
    original = store.publish(run['id'], draft(source['id']))
    packet = analyst.packet(store, run['id'], store.root/'revision-writer', base_version=original['id'],
                            writer_protocol=writer.PROTOCOL, feedback=['修改措辞'], **inputs)
    config = {'native_role':'analyst','run_id':run['id'],'packet_root':str(packet['root']),
              'result_file':str(packet['root'].parent/'draft.json'),'attempt_id':'rev-attempt'}
    read = run_tool(store,config,'read_draft',{'field':'body'})
    assert read['ok'], read
    body = json.loads(read['content'][0]['text'])
    stored = json.loads(original['editor_document'])
    assert body['items'] == stored['content']
    assert body['revision']
    assert not run_tool(store,config,'write_report',{'title':'覆盖','markdown':'不允许整稿覆盖'})['ok']


def test_markdown_keeps_alignment_strike_and_literal_code():
    doc = writer.compile_markdown('|A|B|\n|:---|---:|\n|~~旧~~新|`[@src_literal]`|')
    from briefloop.document_model import source_ids
    assert source_ids(doc) == []
    cells = doc['content'][0]['content'][1]['content']
    assert cells[0]['attrs']['textAlign'] == 'left'
    assert cells[1]['attrs']['textAlign'] == 'right'
    assert cells[0]['content'][0]['content'][0]['marks'] == [{'type':'strike'}]


def test_evidence_tools_have_flat_typed_inputs_and_save_records(tmp_path):
    from pathlib import Path
    from briefloop.native_roles import run_tool, runner_tool_specs
    store,run,source,config = setup_writer(tmp_path)
    task = Path(config['packet_root'])/'input.json';payload=json.loads(task.read_text())
    payload['writer_input_protocol']=writer.PROTOCOL;task.write_text(json.dumps(payload))
    specs={t['name']:t for t in runner_tool_specs('analyst',config=config)}
    assert 'update_draft_evidence' not in specs
    for name in ('update_citations','update_number_bindings','update_temporal_claims'):
        schema=specs[name]['parameters']
        assert schema['type']=='object' and 'base_revision' in schema['properties']
        assert 'anyOf' not in schema and schema['properties']['records']['type']=='array'
    saved=writer.write_report(store,config,{'title':'报告','markdown':f'收入同比增长20%。[@{source["id"]}]'})
    result=run_tool(store,config,'update_citations',{'base_revision':saved['revision'],
        'records':[{'source_id':source['id'],'locator':'line 1','excerpt':store.source_text(source['id'])}]})
    assert result['ok'],result
    report=json.loads(result['content'][0]['text'])
    assert drafts._candidate(store,config,{'revision':report['revision']})['draft']['citations'][0]['source_id']==source['id']
    numbers=run_tool(store,config,'update_number_bindings',{'base_revision':report['revision'],
        'records':[{'label':'收入','value':1200,'unit':'万元','source_id':source['id'],
                    'locator':'line 1','source_excerpt':store.source_text(source['id'])}]})
    assert numbers['ok'],numbers
    numbered=json.loads(numbers['content'][0]['text'])
    removed=run_tool(store,config,'update_number_bindings',{'base_revision':numbered['revision'],
        'remove_keys':numbered['record_keys']})
    assert removed['ok'],removed
    final=json.loads(removed['content'][0]['text'])
    value=drafts._candidate(store,config,{'revision':final['revision']})['draft']
    assert value['number_bindings']==[] and value['citations'][0]['source_id']==source['id']



def test_markdown_prompt_preserves_content_rules_and_frozen_protocol(tmp_path):
    from briefloop import analyst
    from test_native_analyst import setup
    store,run,source,inputs=setup(tmp_path)
    class Captured(Exception): pass
    class Runtime:
        def execute(self,job,prompt,folder):
            assert '保留原始 records' in prompt
            assert '表格内事实的引用放在相应单元格' in prompt
            assert 'writer_input_v1' in prompt
            assert '将完整 BriefDraft 原子写入' not in prompt
            raise Captured()
    folder=store.root/'new-protocol-run'
    for requested in ('writer_input_v1','rich_json_v1'):
        job={'id':'job_protocol','kind':'generate','payload':json.dumps({'run_id':run['id'],
            'agent_backend':'briefloop-native','runtime':{'model':'fake/writer'},'writer_input_protocol':requested})}
        with pytest.raises(Captured):
            analyst.run(store,Runtime(),job,run['id'],folder,'briefloop-native',**inputs)
    assert json.loads((folder/'packet/input.json').read_text())['writer_input_protocol']=='writer_input_v1'
