"""Bounded behavior checks: editorial length advice never hides an ordinary draft."""
from io import BytesIO
import json

from docx import Document
import pytest

from briefloop import analyst
from briefloop.deliverable_spec import resolve, reader_contract_schema, instructions
from briefloop.draft_checks import inspect_draft
from briefloop.document_model import markdown_document
from briefloop.exports import docx_bytes, reader_markdown
from briefloop.models import Requirements
from briefloop.native_roles import run_tool
from briefloop.store import Store


def test_legacy_max_stays_soft_and_reader_contract_fingerprint_is_unchanged():
    old={'title':'周报','objective':'说明采用条件','target_words':10,'max_words':20}
    req=Requirements(**old)
    assert req.length_mode=='soft' and req.length_requirement is None
    legacy=reader_contract_schema(resolve(old))['properties']['source_fingerprint']['const']
    explicit_soft=reader_contract_schema(resolve({**old,'length_mode':'soft','length_requirement':None}))['properties']['source_fingerprint']['const']
    assert legacy==explicit_soft
    for role in ('analyst','revision','evaluator','reviewer'):
        guide=instructions(resolve(old),role)
        assert '篇幅模式为 soft' in guide and '不为凑字数扩写' in guide
        assert '仅凭超出预设判失败' in guide


def test_strict_needs_a_recorded_source_and_user_quote_must_match_original_input():
    base={'title':'周报','objective':'只写采用条件，不得超过20字','length_mode':'strict','max_words':20}
    with pytest.raises(ValueError,match='用户要求来源'):Requirements(**base)
    quote={'kind':'user_quote','text':'不得超过20字'}
    req=Requirements(**base,length_requirement=quote)
    assert req.target_words==20 and req.length_requirement.text==quote['text']
    with pytest.raises(ValueError,match='逐字'):
        Requirements(**base,length_requirement={**quote,'text':'不得超过10字'})
    with pytest.raises(ValueError,match='明确上限'):
        Requirements(title='周报',objective='说明',length_mode='strict',length_requirement=quote)
    ui=Requirements(title='周报',objective='说明',max_words=20,length_mode='strict',
                    length_requirement={'kind':'user_selection','text':'在报告需求中明确选择严格上限：20 字'})
    assert ui.length_requirement.kind=='user_selection'
    assert resolve(req.model_dump())['length_requirement']==quote
    assert reader_contract_schema(resolve(req.model_dump()))!=reader_contract_schema(resolve({**base,'length_mode':'soft'}))


@pytest.mark.parametrize('mode',['soft','strict'])
def test_over_range_draft_still_checks_submits_saves_and_exports_with_conditions(tmp_path,mode):
    store=Store(tmp_path/'workspace')
    text='安装需要使用指定分支，公开测试不等于正式可用。'
    source=store.add_source('安装说明',text)
    req={'title':'采用条件','objective':'说明安装条件','target_words':3,'max_words':5,'allow_web':False}
    if mode=='strict':req.update(length_mode='strict',length_requirement={'kind':'user_selection','text':'在报告需求中明确选择严格上限：5 字'})
    run=store.create_run(req,[source['id']]);before=run['requirements']
    packet=analyst.packet(store,run['id'],store.root/'writer',plan={'draft_structure':['条件']},
        research={'sources':[{'source_id':source['id'],'locator':'line 1','excerpt':text,'facts':[text],'coverage_status':'complete'}],'gaps':[]})
    config={'native_role':'analyst','run_id':run['id'],'packet_root':str(packet['root']),
            'result_file':str(packet['root'].parent/'draft.json'),'attempt_id':'length-policy'}
    value={'title':'采用条件','editor_document':markdown_document(text+f" [@{source['id']}]"),
           'citations':[{'source_id':source['id'],'locator':'line 1','excerpt':text}]}
    def call(name,args):
        result=run_tool(store,config,name,args)
        assert result['ok'],result
        return json.loads(result['content'][0]['text'])
    revision=call('save_draft',value)['revision']
    checked=call('check_draft',{'revision':revision})['diagnostics']
    assert checked['length']['over_limit'] is True
    assert checked['length']['strict_exceeded']==(mode=='strict')
    items=checked['warnings'] if mode=='strict' else checked['notes']
    note=next(item for item in items if item['code']=='over_limit')
    assert note['kind']==('explicit_requirement' if mode=='strict' else 'advisory')
    if mode=='soft':assert checked['status']=='checks_completed'
    else:assert note['requirement']==req['length_requirement']
    submitted=run_tool(store,config,'submit_draft',{'revision':revision})
    assert submitted['ok'] and submitted['settle'],submitted
    accepted=json.loads((packet['root'].parent/'draft.json').read_text(encoding='utf-8'))
    saved=store.publish(run['id'],accepted)
    assert text in saved['markdown'] and source['id'] in saved['markdown']
    body=reader_markdown(store,saved)
    document=Document(BytesIO(docx_bytes(body)))
    assert text in '\n'.join(paragraph.text for paragraph in document.paragraphs)
    assert store.one('runs',run['id'])['requirements']==before
    assert store.brief_view(saved['id'])['length_stats']['length_mode']==mode


def test_under_target_does_not_become_an_error_or_hide_real_citation_error():
    result=inspect_draft({'title':'简报','markdown':'条件已完整说明。'}, {'target_words':100,'max_words':120})
    assert result['status']=='checks_completed'
    assert result['notes'][0]['code']=='below_target'
    result=inspect_draft({'title':'简报','markdown':'条件已完整说明。 [@missing]'}, {'target_words':1,'max_words':2})
    assert result['status']=='needs_attention'
    assert [item['code'] for item in result['warnings']]==['citation_location_missing']
    assert result['notes'][0]['code']=='over_limit'
