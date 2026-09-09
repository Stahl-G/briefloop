import json
import subprocess
import sys
import threading
import urllib.request
from io import BytesIO
from zipfile import ZipFile
import pytest
from briefloop.store import Store
from briefloop.report_tools import prepare_for_run, report_details
from briefloop.server import make_server


def example(store):
    source=store.add_source('合成输入','仅供功能验证。指标A本期12、上期10；2027预测12、2028预测15。')
    ref=store.add_source('合成风格参考','历史报告结构。不能作为本期事实。')
    req={'title':'合成行业报告','objective':'验证数据保存、缺口与下载','report_profile':'industry_periodic',
         'industry':'示例行业','organization':'示例组织','report_date':'2026-09-10','reference_source_ids':[ref['id']]}
    run=store.create_run(req,[source['id']])
    record={'metric':'指标A','unit':'台','current':12,'current_date':'2026-09-08','previous':10,
            'previous_date':'2026-09-01','source_id':source['id'],'locator':'第1行','comparison':'pct',
            'comparable':True,'previous_unit':'台','previous_tax_basis':'','previous_category':'actual'}
    return run,source,ref,record


def test_report_data_cli_save_reference_boundary_and_revision(tmp_path):
    store=Store(tmp_path/'workspace');run,source,ref,record=example(store)
    raw={'records':[record]};path=tmp_path/'input.json';path.write_text(json.dumps(raw))
    result=subprocess.run([sys.executable,'-m','briefloop','tool','--workspace',str(store.root),'prepare-report-data',
        '--run',run['id'],'--file',str(path),'--output',str(tmp_path/'prepared.json')],capture_output=True,text=True,check=True)
    prepared=json.loads(result.stdout);assert prepared['calculations'][0]['change']==20
    assert json.loads((tmp_path/'prepared.json').read_text())==prepared
    with pytest.raises(ValueError,match='不是本轮证据'):
        prepare_for_run(store,run['id'],{'records':[{**record,'source_id':ref['id']}]})
    with pytest.raises(ValueError,match='风格参考'):
        store.publish(run['id'],{'title':'错误引用','markdown':'正文','citations':[{'source_id':ref['id']}]})
    with pytest.raises(ValueError,match='同时'):
        store.create_run(json.loads(run['requirements']),[ref['id']])
    draft=store.publish(run['id'],{'title':'报告','markdown':prepared['markdown'],'report_data':raw,'gaps':['缺少区域B需求数据；影响覆盖；建议补公开统计。']})
    restored=Store(store.root).one('briefs',draft['id']);assert json.loads(restored['detail'])['report_data']['records'][0]['current_date']=='2026-09-08'
    revised=store.revise(draft['id'],draft['markdown'].replace('12 台','13 台'))
    assert json.loads(revised['detail'])['report_data_needs_review']
    assert any('手动修订' in x for x in report_details(store,revised)['gaps'])


def test_real_http_profile_template_data_and_docx_without_model(tmp_path):
    server=make_server(tmp_path/'workspace',port=0,paused=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    def get(path):
        with urllib.request.urlopen(base+path) as response:return response.read()
    try:
        assert 'industry_periodic' in get('/').decode()
        assert json.loads(get('/api/report-data-template'))['records']==[]
        assert 'records' in json.loads(get('/api/report-data-schema'))['properties']
        run,source,ref,record=example(server.store)
        records=[{**record,'category':'forecast','comparison':'none','previous':None,'current_date':f'{year}-12-31',
                  'current':value,'as_of':'2026-09-08'} for year,value in [(2027,12),(2028,15)]]
        draft=server.store.publish(run['id'],{'title':'合成行业报告','markdown':'## 核心摘要\n\n预测指标12台。\n\n## 数据缺口\n\n缺少区域B。',
            'report_data':{'records':records},'gaps':['缺少区域B。']})
        data=json.loads(get('/api/report-data?version='+draft['id']));assert data['data']['records'][0]['current']==12
        original=get('/api/download?format=docx&version='+draft['id'])
        with ZipFile(BytesIO(original)) as archive:
            # Charts are chosen explicitly by the agent; raw forecast data no longer forces one.
            assert not any(p.startswith('word/media/') for p in archive.namelist())
            assert b'11906' in archive.read('word/document.xml')
        revision=server.store.revise(draft['id'],draft['markdown'].replace('12','14'))
        revised=get('/api/download?format=docx&version='+revision['id'])
        with ZipFile(BytesIO(revised)) as archive:assert not any(p.startswith('word/media/') for p in archive.namelist())
        assert not server.worker.thread.is_alive()
    finally:
        server.shutdown();thread.join();server.harness.close();server.server_close();server.workspace_lock.close()
