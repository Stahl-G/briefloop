"""PowerShell UTF-8 BOM files remain valid Scout and Analyst handoffs."""
import codecs
import json
from pathlib import Path
import subprocess
import sys

import pytest

from briefloop.interactive_runtime import _usable_output
from briefloop.runtime import Worker, assessment_prompt
from briefloop.store import Store


@pytest.fixture
def cp1252_default(monkeypatch):
    """Exercise Windows' Western locale on every CI host using real text I/O."""
    original_open=Path.open
    def cp1252_open(self,mode='r',buffering=-1,encoding=None,errors=None,newline=None):
        if 'b' not in mode and encoding in (None,'locale'):
            encoding='cp1252'
        return original_open(self,mode,buffering,encoding,errors,newline)
    monkeypatch.setattr(Path,'open',cp1252_open)


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
def test_analyst_draft_is_checked_and_published_with_shell_encoding(tmp_path,encoding,cp1252_default):
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
    folder=store.root/'jobs'/job['id']
    for name in ('draft.schema.json','scout.schema.json','assessment.schema.json'):
        text=(folder/name).read_bytes().decode('utf-8')
        schema=json.loads(text)
        assert schema['required']
        assert len(text.splitlines())>1 and max(map(len,text.splitlines()))<2000
    from briefloop.models import Assessment
    assessment=json.loads((folder/'assessment.schema.json').read_bytes().decode('utf-8'))
    assert assessment==Assessment.model_json_schema()
    assert 'summary' in assessment['required']
    snapshots=json.loads((folder/'generated-source-snapshots.json').read_bytes().decode('utf-8'))
    assert result['version_id'] in snapshots
    # The evaluator receives a separate readable packet, including the tail
    # fields that a single-line native read previously truncated.
    evaluation=folder/'evaluation';evaluation.mkdir()
    assessment_prompt(store,brief,evaluation,'opencode')
    for packet in (folder/'input.json',evaluation/'input.json'):
        text=packet.read_bytes().decode('utf-8')
        assert len(text.splitlines())>1
        assert isinstance(json.loads(text),dict)


def test_rejected_draft_stays_readable_under_cp1252_default(tmp_path,cp1252_default):
    store=Store(tmp_path/'中文工作区')
    source=store.add_source('材料','指标为十二台。')
    run=store.create_run({'title':'本期报告','objective':'核对材料'},[source['id']])
    job=store.enqueue('generate',{'run_id':run['id']})
    class InvalidRuntime:
        def execute(self,job,prompt,folder,on_tick=lambda:None,**kwargs):
            (folder/'draft.json').write_text(json.dumps({'markdown':'缺少标题的正文'},ensure_ascii=False),encoding='utf-8-sig')
            on_tick()
            return {}
    with pytest.raises(ValueError,match='title'):
        Worker(store,InvalidRuntime()).generate(job,score=False)
    rejected=store.root/'jobs'/job['id']/'draft-invalid.json'
    assert json.loads(rejected.read_bytes().decode('utf-8'))['markdown']=='缺少标题的正文'
    assert not rejected.read_bytes().startswith(codecs.BOM_UTF8)
    assert store.rows('SELECT id FROM briefs WHERE run_id=?',(run['id'],))==[]
