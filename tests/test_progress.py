"""Progress projection must follow the current evaluation directory layout."""
import json
from briefloop.progress import ProgressTracker
from briefloop.store import Store


def test_progress_reports_the_evaluation_assessment(tmp_path):
    store=Store(tmp_path/'workspace')
    job=store.enqueue('generate',{})
    folder=store.root/'jobs'/job['id'];folder.mkdir(parents=True)
    (folder/'events.jsonl').write_text('')
    tracker=ProgressTracker(store,job['id'],folder)
    tracker.update()
    (folder/'evaluation').mkdir()
    (folder/'evaluation'/'assessment.json').write_text('{}')
    tracker.update()
    row=store.rows("SELECT data FROM events WHERE job_id=? AND kind='runtime_progress' ORDER BY seq DESC LIMIT 1",(job['id'],))
    assert json.loads(row[0]['data'])['stage']=='评分已返回，正在保存结果'
