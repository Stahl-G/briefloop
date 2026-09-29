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
