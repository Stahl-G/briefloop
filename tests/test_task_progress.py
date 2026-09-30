import json
from briefloop.store import Store
from briefloop.task_progress import summary


def test_fast_report_card_does_not_hide_provider_retry_stage(tmp_path):
    from briefloop.store import Store
    from briefloop.task_progress import summary
    store=Store(tmp_path)
    store.set_meta('settings',{**store.settings(),'model':'synthetic'})
    source=store.add_source('Synthetic','Ordered 30, received 26.')
    run=store.create_run({'title':'Synthetic','objective':'Read locally','completion_mode':'fast'},[source['id']])
    job=store.enqueue('generate',{'run_id':run['id']})
    store.update_job(job['id'],'running')
    store.event(job['id'],'runtime_progress',{'stage':'模型服务正在重试','runtime_notice':True,'last_activity':'2026-09-30T09:49:39+00:00'})
    assert summary(store,job['id'])['stage']=='模型服务正在重试'
