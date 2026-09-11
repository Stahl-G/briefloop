"""One local boundary check: public research can begin empty and retain new sources."""
import json
from briefloop.store import Store, dump
from briefloop.runtime import generation_prompt, assessment_prompt
from briefloop.chat_tools import chat_instructions
from briefloop.scout_tools import join_scouts


def test_public_research_empty_inputs_and_actual_network_instructions(tmp_path):
    store=Store(tmp_path/'workspace')
    run=store.create_run({'title':'公开市场周报','objective':'分析本周公开披露','allow_web':True,'period':'本周'},[])
    folder=store.root/'jobs'/'synthetic';folder.mkdir()
    prompt=generation_prompt(store,run,folder)
    assert json.loads((folder/'input.json').read_text())['sources']==[]
    payload=json.loads((folder/'input.json').read_text())
    from pathlib import Path
    slots=payload['scout_slots']
    assert len(slots)==store.settings()['max_parallel']
    assert len({slot['result_file'] for slot in slots})==len(slots)
    assert all(Path(slot[key]).is_absolute() for slot in slots for key in ('directory','result_file','schema_path','scout_contract_path'))
    assert all(Path(slot['directory']).is_dir() and Path(slot['schema_path']).is_file() for slot in slots)
    assert all(Path(slot['result_file']).parent==Path(slot['directory']) for slot in slots)
    assert '不要假设 host 自动隔离工作目录' in prompt
    assert '至少安排一个 Scout' in prompt and 'add-url --run '+run['id'] in prompt
    assert 'read-source --id SOURCE_ID' in prompt and 'acquired source IDs' in prompt
    assert all(mark in prompt for mark in ('侦察','聚焦','补缺'))
    assert '获取失败要按原因换路径' in prompt and '不把所有查询限定在官网' in prompt
    assert '不重复已经失败或已充分覆盖的相近查询' in prompt
    # Mimic registered acquisition without networking; join and scorer retain it.
    acquired=store.add_source('官方披露','预计下一季度交付 10 台。',url='https://example.com/disclosure')
    store.attach_source(run['id'],acquired['id'])
    result=folder/'scout-result.json'
    result.write_text(dump({'sources':[{'source_id':acquired['id'],'locator':'第1段','excerpt':'预计下一季度交付 10 台。','facts':['预计交付'],'coverage_status':'covered'}],'gaps':[]}))
    assert join_scouts(store,[str(result)])['sources'][0]['source_id']==acquired['id']
    brief=store.publish(run['id'],{'title':'周报','markdown':'预计下一季度交付。','citations':[{'source_id':acquired['id'],'locator':'第1段'}]})
    score=folder/'scorer';score.mkdir()
    assessment_prompt(store,brief,score)
    assert [s['id'] for s in json.loads((score/'input.json').read_text())['sources']]==[acquired['id']]
    runtime={'model':'gpt-5.6-luna','effort':'high'}
    disabled=chat_instructions(store,runtime,allow_web=False)
    enabled=chat_instructions(store,runtime,allow_web=True)
    assert '实际联网状态：未开启' in disabled and '不得通过后台任务绕过' in disabled
    assert '实际联网状态：已开启' in enabled and 'source_ids=[]' in enabled
    assert '预计/实际' in enabled
    assert '实际联网状态：未开启' in chat_instructions(store,{},internal=True,allow_web=False)


def test_search_provider_is_frozen_and_tavily_provenance_is_explicit(tmp_path):
    store=Store(tmp_path/'workspace')
    store.set_meta('settings',{**store.settings(),'search_provider':'tavily'})
    run=store.create_run({'title':'市场周报','objective':'核对公开披露','allow_web':True},[])
    job=store.enqueue('generate',{'run_id':run['id']})
    store.set_meta('settings',{**store.settings(),'search_provider':'native'})
    payload=json.loads(store.one('jobs',job['id'])['payload'])
    assert payload['search_provider']=='tavily'
    assert store.search_provider_for_run(run['id'])=='tavily'
    folder=store.root/'jobs'/'provider-check';folder.mkdir()
    generation_prompt(store,{**run,'search_provider':payload['search_provider']},folder)
    retrieval=json.loads((folder/'input.json').read_text())['retrieval_skill']
    from pathlib import Path
    skill_text=Path(retrieval['path']).read_text()
    assert 'tavily-search --run '+run['id'] in skill_text
    assert 'tavily-extract --run '+run['id'] in skill_text
    assert '不是原网站字节' in skill_text and '不调用 Tavily Research' in skill_text
    assert json.loads((folder/'input.json').read_text())['search_provider']=='tavily'
    # Old jobs keep their prior Codex behavior even if settings now select Tavily.
    payload.pop('search_provider')
    with store.tx() as connection:
        connection.execute('UPDATE jobs SET payload=? WHERE id=?',(dump(payload),job['id']))
    store.set_meta('settings',{**store.settings(),'search_provider':'tavily'})
    assert store.search_provider_for_run(run['id'])=='native'
    text=chat_instructions(store,{'model':'gpt-5.6-luna','effort':'high'},allow_web=False)
    assert '当前正式研究搜索源：Tavily' in text
    assert '实际联网状态：未开启' in text


def test_builtin_tavily_skill_only_enters_enabled_scout_context(tmp_path, monkeypatch):
    from importlib.resources import files
    from pathlib import Path
    asset=files('briefloop').joinpath('skill_assets','tavily','SKILL.md')
    assert asset.is_file() and 'name: tavily' in asset.read_text()
    skill_text=asset.read_text()
    assert '检索节奏' in skill_text and all(mark in skill_text for mark in ('侦察','聚焦','补缺'))
    assert '获取失败：按原因换路径' in skill_text and '不永久拉黑整个域名' in skill_text
    monkeypatch.setenv('TAVILY_API_KEY','SYNTHETIC_SECRET_DO_NOT_INJECT')
    store=Store(tmp_path/'workspace')
    source=store.add_source('initial','已有公开资料')
    for provider,allowed in [('tavily',True),('native',True),('tavily',False)]:
        run=store.create_run({'title':'研究','objective':'核对公开资料','allow_web':allowed},[source['id']])
        folder=store.root/'jobs'/run['id'];folder.mkdir()
        prompt=generation_prompt(store,{**run,'search_provider':provider},folder)
        payload=json.loads((folder/'input.json').read_text())
        if provider=='tavily' and allowed:
            binding=payload['retrieval_skill']
            assert binding['target_roles']==['scout']
            content=Path(binding['path']).read_text()
            dispatch=Path(binding['dispatch_prompt_path']).read_text()
            assert Path(binding['path']).is_absolute() and binding['path'] in dispatch
            assert content not in dispatch
            assert content in payload['role_skills']['scout']['instructions']
            assert 'retrieval_skill_path' not in payload['role_skills'].get('analyst',{})
            assert '{tool}' not in content and '{run_id}' not in content
            assert run['id'] in content and '公开确认已读' in prompt
            assert 'SYNTHETIC_SECRET_DO_NOT_INJECT' not in prompt+content+dispatch+dump(payload)
            assert 'Analyst、Evaluator、Maintainer' in prompt
        else:
            assert 'retrieval_skill' not in payload
            assert not (folder/'capabilities'/'tavily'/'SKILL.md').exists()
            assert 'tavily-search' not in prompt


def test_evaluator_initial_sources_follow_citations_and_keep_full_index(tmp_path):
    store=Store(tmp_path/'workspace')
    sources=[store.add_source('source-'+str(i),'body-'+str(i),error='fetch failed' if i==5 else None) for i in range(6)]
    run=store.create_run({'title':'brief','objective':'review coverage'},[row['id'] for row in sources])
    citations=[{'source_id':sources[i]['id'],'locator':'line 1','excerpt':'body'} for i in (2,0,2)]
    gaps=['important gap','g'*400]
    brief=store.publish(run['id'],{'title':'brief','markdown':'body','citations':citations,'gaps':gaps})
    folder=store.root/'jobs'/'evaluator-pack';folder.mkdir()
    prompt=assessment_prompt(store,brief,folder)
    pack=json.loads((folder/'input.json').read_text())
    assert [row['id'] for row in pack['sources']]==[sources[2]['id'],sources[0]['id']]
    assert pack['brief']['citations']==citations
    assert len(pack['gaps'][1])==240
    index=json.loads((folder/'source-index.json').read_text())
    assert {row['id'] for row in index['sources']}=={row['id'] for row in sources}
    assert index['sources'][5]['status']=='failed' and index['gaps']==gaps
    assert '需要其他材料时' in prompt
    assert store.one('briefs',brief['id'])==brief
