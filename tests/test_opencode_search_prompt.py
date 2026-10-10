"""The frozen search provider must agree with the OpenCode Scout handoff."""
import json
from pathlib import Path

import pytest

from briefloop.runtime import generation_prompt
from briefloop.store import Store


@pytest.fixture(autouse=True)
def _v1_search_contract(monkeypatch):
    monkeypatch.setattr('briefloop.opencode_version.installed_major',lambda:1)


@pytest.mark.parametrize('provider,allowed',[('tavily',True),('native',True),('tavily',False)])
def test_generation_packet_is_utf8_with_cp1252_default(tmp_path,monkeypatch,provider,allowed):
    from briefloop.deliverable_spec import instructions, reader_contract_schema, resolve
    from briefloop.models import Requirements, ScoutResult

    store=Store(tmp_path/'工作区 中文')
    source=store.add_source('公司披露','公司甲本期交付十二台设备。')
    run=store.create_run({'title':'公司对比报告','objective':'比较公司甲与公司乙的公开披露',
                          'allow_web':allowed},[source['id']])
    folder=store.root/'jobs'/'中文任务包';folder.mkdir()
    # Exercise real non-UTF-8 text I/O on every host. Explicit encodings must
    # survive both the prompt builder and its actual material-loading calls.
    original_open=Path.open
    def cp1252_open(self,mode='r',buffering=-1,encoding=None,errors=None,newline=None):
        if 'b' not in mode and encoding in (None,'locale'):
            encoding='cp1252'
        return original_open(self,mode,buffering,encoding,errors,newline)
    monkeypatch.setattr(Path,'open',cp1252_open)

    prompt=generation_prompt(store,{**run,'search_provider':provider},folder,backend='opencode')
    packet={path.relative_to(folder).as_posix():path.read_bytes().decode('utf-8')
            for path in folder.rglob('*') if path.is_file()}
    payload=json.loads(packet['input.json'])
    assert payload['requirements']['title']=='公司对比报告'
    assert payload['sources'][0]['name']=='公司披露'
    assert payload['search_provider']==provider
    deliverable=resolve(Requirements.model_validate(json.loads(run['requirements'])).model_dump())
    assert payload['deliverable_spec']==deliverable
    from briefloop.report_time import instructions as time_instructions
    from briefloop.research_handoff import PLANNING_GUIDE
    temporal_note=time_instructions(payload['requirements']['time_context'])
    assert packet['analyst-writing.md'].replace('\r\n','\n')==instructions(deliverable,role='analyst')+'\n'+temporal_note+'\n'+PLANNING_GUIDE
    assert packet['scout-contract.md'].replace('\r\n','\n')==instructions(deliverable,role='scout')+'\n'+temporal_note
    assert json.loads(packet['reader_contract.schema.json'])==reader_contract_schema(deliverable)
    expected={'input.json','analyst-writing.md','scout-contract.md','reader_contract.schema.json',
              'scout-context.json','analyst-context.json'}
    for role in ('scout', 'analyst'):
        context=json.loads(packet[role+'-context.json'])
        assert context['role']==role
        assert context['purpose']['objective']=='比较公司甲与公司乙的公开披露'
        assert '证据' in context['knowledge']['meaning'] or '原文' in context['knowledge']['meaning']
    for slot in payload['scout_slots']:
        name=Path(slot['schema_path']).relative_to(folder).as_posix()
        expected.add(name)
        assert json.loads(packet[name])==ScoutResult.model_json_schema()
        assert Path(slot['scout_contract_path'])==folder/'scout-contract.md'
    if provider=='tavily' and allowed:
        expected.update({'capabilities/tavily/SKILL.md','capabilities/tavily/scout-dispatch.md'})
        assert '开始时完整读取一次' in packet['capabilities/tavily/scout-dispatch.md']
        assert 'web-search --run '+run['id'] in packet['capabilities/tavily/SKILL.md']
        assert payload['retrieval_skill']['path'] in prompt
    assert set(packet)==expected
