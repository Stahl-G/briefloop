"""PowerShell UTF-8 BOM files remain valid Scout and Analyst handoffs."""
import codecs
import json
import subprocess
import sys

import pytest

from briefloop.interactive_runtime import _usable_output
from briefloop.runtime import Worker
from briefloop.store import Store


def run_tool(workspace, *arguments):
    result=subprocess.run([sys.executable,'-X','utf8','-m','briefloop','tool',
                           '--workspace',str(workspace),*map(str,arguments)],
                          capture_output=True,encoding='utf-8',timeout=30)
    assert result.returncode==0,result.stderr+result.stdout
    return json.loads(result.stdout)


@pytest.mark.parametrize('encoding',['utf-8','utf-8-sig'])
def test_scout_join_reads_shell_json_and_writes_plain_utf8(tmp_path,encoding):
    store=Store(tmp_path/'中文工作区')
    source=store.add_source('材料','指标为十二台。')
    scout=store.root/'scout.json'
    scout.write_text(json.dumps({'sources':[{'source_id':source['id'],
        'coverage_status':'ok','excerpt':'指标为十二台。'}],
        'search_summary':'已核对材料'},ensure_ascii=False),encoding=encoding)
    joined=store.root/'joined.json'
    result=run_tool(store.root,'join-scouts','--files',scout,'--output',joined)
    assert result['sources'][0]['excerpt']=='指标为十二台。'
    assert json.loads(joined.read_text(encoding='utf-8'))==result
    assert not joined.read_bytes().startswith(codecs.BOM_UTF8)


@pytest.mark.parametrize('encoding',['utf-8','utf-8-sig'])
def test_analyst_draft_is_checked_and_published_with_shell_encoding(tmp_path,encoding):
    store=Store(tmp_path/'中文工作区')
    source=store.add_source('材料','指标为十二台。')
    run=store.create_run({'title':'本期报告','objective':'核对材料'},[source['id']])
    job=store.enqueue('generate',{'run_id':run['id']})
    class ShellRuntime:
        def execute(self,job,prompt,folder,on_tick=lambda:None,**kwargs):
            draft=folder/'draft.json'
            draft.write_text(json.dumps({'title':'本期报告','markdown':'指标为十二台。',
                'citations':[{'source_id':source['id']}]},ensure_ascii=False),encoding=encoding)
            assert run_tool(store.root,'check-draft','--file',draft)['status']=='ok'
            assert _usable_output(job,folder,store)
            on_tick()
            return {}
    result=Worker(store,ShellRuntime()).generate(job,score=False)
    brief=store.one('briefs',result['version_id'])
    assert brief['markdown']=='指标为十二台。'
    assert len(store.rows('SELECT id FROM briefs WHERE run_id=?',(run['id'],)))==1
