"""Only the core personal-tool risks. Model fixtures do not prove live quality."""
import json
from pathlib import Path
import tempfile
import unittest
from briefloop.store import Store, Conflict
from briefloop.runtime import Worker
from briefloop.learning import enqueue_feedback, apply_accepted
from briefloop.learning_budget import plan as learning_plan
from wikiskill import feedback_loop, native_agents


class CoreBehavior(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(self.tmp.name)
        src=self.store.add_source('synthetic','预计交付，尚未完成。')
        self.run=self.store.create_run({'title':'测试','objective':'保留交付状态'},[src['id']])
        self.brief=self.store.publish(self.run['id'],{'title':'测试','markdown':'预计交付。'})

    def test_saved_revision_and_assessment_do_not_replace_original(self):
        s=self.store;b=self.brief
        edit=s.revise(b['id'],'预计交付，尚未完成。')
        self.assertEqual(s.one('briefs',b['id'])['markdown'],'预计交付。')
        with self.assertRaises(Conflict):s.revise(b['id'],'并发覆盖')
        bad={'brief_hash':b['hash'],'summary':'测试','overall':'达到要求',**{k:3 for k in ('evidence','coverage','analysis','expression')}}
        with self.assertRaises(Conflict):s.assess(edit['id'],bad)
        result=s.assess(edit['id'],{**bad,'brief_hash':edit['hash']})
        self.assertEqual(result['version_id'],edit['id'])
        self.assertEqual(len(Store(self.tmp.name).snapshot()['briefs']),2)


    def test_model_change_keeps_the_frozen_resume_attempt(self):
        s=self.store
        s.set_meta('settings',{**s.settings(),'model':'gpt-6-astra','reasoning_effort':'medium'})
        old=s.enqueue('generate',{'run_id':self.run['id']});s.update_job(old['id'],'cancelled')
        frozen=s.one('jobs',old['id'])['payload']
        s.set_meta('settings',{**s.settings(),'model':'gpt-5.6-luna','reasoning_effort':'high'})
        resumed=Worker(s).resume(old['id'])
        self.assertEqual(resumed['id'],old['id'])
        kept={k:v for k,v in json.loads(s.one('jobs',old['id'])['payload']).items() if k!='attempt'}
        self.assertEqual(kept,json.loads(frozen))


if __name__=='__main__':unittest.main()


def test_polled_state_lists_versions_without_bodies(tmp_path):
    store=Store(tmp_path)
    source=store.add_source('synthetic','材料')
    run=store.create_run({'title':'长期周报','objective':'核对','target_words':2,'max_words':3},[source['id']])
    original=store.publish(run['id'],{'title':'长期周报','markdown':'历史正文包含独特词 AlphaCanary。'})
    edited=store.revise(original['id'],'新的正文')
    listed={row['id']:row for row in store.snapshot()['briefs']}
    assert set(listed)=={original['id'],edited['id']}
    for row in listed.values():
        assert not {'markdown','editor_document','length_stats'}&set(row) and row['hash'] and row['detail']
    assert listed[original['id']]['excerpt'].startswith('历史正文')
    view=store.brief_view(original['id'])
    assert view['markdown']==original['markdown'] and view['length_stats']['over_limit'] is True
    # Full-text report search still reaches historical bodies, without sending them.
    assert store.search_briefs('alphacanary')==[run['id']] and store.search_briefs('absent')==[]


def test_polled_state_keeps_old_active_jobs_and_bounds_inactive_history(tmp_path):
    store=Store(tmp_path)
    old_queued=store.enqueue('source_refresh',{})
    old_running=store.enqueue('source_refresh',{})
    store.update_job(old_running['id'],'running')
    store.event(old_running['id'],'learning_progress',{'phase':'synthetic-running'})
    history=[]
    for index in range(35):
        job=store.enqueue('source_refresh',{})
        store.update_job(job['id'],('complete','failed','cancelled','interrupted')[index%4])
        history.append(job)
    newest=store.enqueue('source_refresh',{})

    jobs=store.snapshot()['jobs']
    expected=[newest['id'],*[job['id'] for job in reversed(history[-30:])],old_running['id'],old_queued['id']]
    assert [job['id'] for job in jobs]==expected
    assert len({job['id'] for job in jobs})==len(jobs)
    assert next(job for job in jobs if job['id']==old_running['id'])['progress']=={'phase':'synthetic-running'}

    # A finished old task returns to the bounded history; no permanent pinning.
    store.update_job(old_running['id'],'complete')
    assert old_running['id'] not in {job['id'] for job in store.snapshot()['jobs']}
