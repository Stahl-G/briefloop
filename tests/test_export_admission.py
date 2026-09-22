import threading
from concurrent.futures import ThreadPoolExecutor

from briefloop import export_jobs
from briefloop.store import Store, dump


def test_concurrent_exports_share_job_and_rebuild_missing_or_damaged_file(tmp_path, monkeypatch):
    store = Store(tmp_path)
    source = store.add_source('Synthetic', '本周两项交付。')
    run = store.create_run({'title': '验收', 'objective': '合成测试'}, [source['id']])
    brief = store.publish(run['id'], {'title': '验收', 'markdown': '# 验收\n\n本周两项交付。'})
    # Independent Store instances model separate request handlers/processes.
    stores = [Store(tmp_path) for _ in range(4)]
    original = export_jobs.export_input
    ready = threading.Barrier(len(stores))
    def concurrent_input(*args, **kwargs):
        result = original(*args, **kwargs)
        ready.wait(timeout=10)
        return result
    monkeypatch.setattr(export_jobs, 'export_input', concurrent_input)
    with ThreadPoolExecutor(max_workers=len(stores)) as pool:
        jobs = list(pool.map(lambda s: export_jobs.enqueue_export(s, brief['id']), stores))
    assert len({job['id'] for job in jobs}) == 1
    monkeypatch.setattr(export_jobs, 'export_input', original)
    job = jobs[0]
    result = export_jobs.generate_word(store, job, threading.Event())
    with store.tx() as c:
        c.execute("UPDATE jobs SET status='complete',result=? WHERE id=?", (dump(result), job['id']))
    assert export_jobs.enqueue_export(store, brief['id'])['id'] == job['id']
    export_jobs.output_path(store, job).write_bytes(b'corrupt')
    rebuilt = export_jobs.enqueue_export(store, brief['id'])
    assert rebuilt['id'] != job['id']
    result = export_jobs.generate_word(store, rebuilt, threading.Event())
    with store.tx() as c:
        c.execute("UPDATE jobs SET status='complete',result=? WHERE id=?", (dump(result), rebuilt['id']))
    export_jobs.output_path(store, rebuilt).unlink()
    assert export_jobs.enqueue_export(store, brief['id'])['id'] != rebuilt['id']
    revised = store.revise(brief['id'], '# 验收\n\n本周三项交付。')
    changed = export_jobs.enqueue_export(store, revised['id'])
    assert changed['id'] not in {job['id'] for job in store.rows("SELECT * FROM jobs WHERE kind='export_docx'") if job['payload'] == rebuilt['payload']}


def test_reader_source_upgrade_invalidates_both_renderer_caches(tmp_path, monkeypatch):
    from briefloop.templates import import_builtin
    store = Store(tmp_path)
    import_builtin(store)
    template_id = store.rows('SELECT id FROM templates LIMIT 1')[0]['id']
    source = store.add_source('Synthetic', '本周两项交付。')
    run = store.create_run({'title': '验收', 'objective': '合成测试'}, [source['id']])
    brief = store.publish(run['id'], {'title': '验收', 'markdown': '# 验收\n\n本周两项交付。'})
    current_input = export_jobs.export_input
    def old_input(*args, **kwargs):
        identity, figures = current_input(*args, **kwargs)
        identity['renderer'] = 22 if identity['requirements'].get('template_id') else 23
        return identity, figures
    for layout in (None, template_id):
        monkeypatch.setattr(export_jobs, 'export_input', old_input)
        old = export_jobs.enqueue_export(store, brief['id'], layout)
        result = export_jobs.generate_word(store, old, threading.Event())
        with store.tx() as c:
            c.execute("UPDATE jobs SET status='complete',result=? WHERE id=?", (dump(result), old['id']))
        assert export_jobs.enqueue_export(store, brief['id'], layout)['id'] == old['id']
        monkeypatch.setattr(export_jobs, 'export_input', current_input)
        upgraded = export_jobs.enqueue_export(store, brief['id'], layout)
        assert upgraded['id'] != old['id']
        assert export_jobs.enqueue_export(store, brief['id'], layout)['id'] == upgraded['id']
        assert export_jobs.output_path(store, old).is_file()
