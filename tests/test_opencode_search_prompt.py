"""The frozen search provider must agree with the OpenCode Scout handoff."""
import json
from pathlib import Path

import pytest

from briefloop.runtime import generation_prompt
from briefloop.store import Store


def test_opencode_frozen_tavily_reaches_both_coordinator_and_scout(tmp_path):
    store=Store(tmp_path/'workspace')
    store.set_meta('settings',{**store.settings(),'agent_backend':'opencode',
                              'model':'synthetic/model','search_provider':'tavily'})
    run=store.create_run({'title':'Company comparison','objective':'Compare public disclosures','allow_web':True},[])
    job=store.enqueue('generate',{'run_id':run['id']})
    frozen=json.loads(job['payload'])
    assert frozen['agent_backend']=='opencode'
    assert frozen['search_provider']=='tavily'
    # Later settings must not rewrite the submitted task's retrieval contract.
    store.set_meta('settings',{**store.settings(),'search_provider':'native'})
    folder=store.root/'jobs'/job['id'];folder.mkdir()
    prompt=generation_prompt(store,{**run,'search_provider':frozen['search_provider']},folder,backend='opencode')
    payload=json.loads((folder/'input.json').read_text(encoding='utf-8'))
    assert '本轮冻结搜索源：Tavily。' in prompt
    assert '本轮冻结搜索源：Opencode 原生搜索' not in prompt
    assert 'Scout 使用 host 的原生网络搜索工具' not in prompt
    assert '实际 task 工具消息优先使用精简任务' in prompt
    assert '公开确认已读' in prompt
    binding=payload['retrieval_skill']
    assert binding['target_roles']==['scout']
    assert binding['path'] in prompt
    skill=Path(binding['path']).read_text(encoding='utf-8')
    dispatch=Path(binding['dispatch_prompt_path']).read_text(encoding='utf-8')
    assert binding['path'] in dispatch
    assert 'tavily-search --run '+run['id'] in skill
    assert 'tavily-extract --run '+run['id'] in skill
    assert skill in payload['role_skills']['scout']['instructions']
    assert 'retrieval_skill_path' not in payload['role_skills'].get('analyst',{})
    assert payload['search_provider']=='tavily'


@pytest.mark.parametrize('provider,allowed',[('native',True),('tavily',False)])
def test_opencode_native_and_no_web_tasks_do_not_receive_tavily_skill(tmp_path,provider,allowed):
    store=Store(tmp_path/'workspace')
    source=store.add_source('Synthetic disclosure','Company A delivered 12 units.')
    run=store.create_run({'title':'Comparison','objective':'Compare available material','allow_web':allowed},[source['id']])
    folder=store.root/'jobs'/'search-check';folder.mkdir()
    prompt=generation_prompt(store,{**run,'search_provider':provider},folder,backend='opencode')
    payload=json.loads((folder/'input.json').read_text(encoding='utf-8'))
    assert 'retrieval_skill' not in payload
    assert not (folder/'capabilities'/'tavily'/'SKILL.md').exists()
    assert '本轮冻结搜索源：Tavily。' not in prompt
    if allowed:
        assert '本轮冻结搜索源：Opencode 原生搜索。' in prompt
    else:
        assert '本轮未允许联网，只处理已登记的材料' in prompt
        assert '不安排公开检索' in prompt
        assert 'Scout 使用 host 的原生网络搜索工具' not in prompt
