"""Only the core personal-tool risks. Model fixtures do not prove live quality."""
import json
from pathlib import Path
import tempfile
import unittest
from briefloop.store import Store, Conflict
from briefloop.runtime import Worker
from briefloop.learning import enqueue_feedback, apply_accepted
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

    def test_feedback_batches_once_and_trials_do_not_change_user_requirements(self):
        s=self.store;s.revise(self.brief['id'],'**预计**交付。')
        self.assertEqual(len(s.rows('SELECT id FROM feedback')),0)
        s.comment(self.brief['id'],'保持交付状态限定')
        job=enqueue_feedback(s);self.assertEqual(json.loads(job['payload'])['k'],1)
        s.set_meta('settings',{**s.settings(),'k':3})
        self.assertEqual(enqueue_feedback(s)['status'],'idle')
        self.assertEqual(json.loads(s.one('jobs',job['id'])['payload'])['k'],1)
        s.create_run({'title':'试验任务','objective':'仅用于验证'},json.loads(self.run['source_ids']),mode='trial',skill_id=None)
        self.assertEqual(s.meta('requirements')['title'],'测试')

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

    def test_refined_draft_becomes_child_version_not_failure(self):
        s=self.store
        job=s.enqueue('generate',{'run_id':self.run['id']})
        class FakeRuntime:
            def execute(self_clone,job,prompt,folder,on_tick=lambda:None,**kwargs):
                (folder/'draft.json').write_text(json.dumps({'title':'测试','markdown':'第一版。'}))
                on_tick()
                (folder/'draft.json').write_text(json.dumps({'title':'测试','markdown':'第一版。修订版。'}))
                return {}
        worker=Worker(s);worker.runtime=FakeRuntime()
        result=worker.generate(job,score=False)
        vid='brief_'+job['id'][4:]
        self.assertEqual(s.one('briefs',vid)['markdown'],'第一版。')
        child=s.one('briefs',result['version_id'])
        self.assertNotEqual(child['id'],vid)
        self.assertEqual(child['parent_id'],vid)
        self.assertEqual(child['markdown'],'第一版。修订版。')
        self.assertEqual(len(s.rows('SELECT id FROM briefs WHERE run_id=?',(self.run['id'],))),3)

    def test_wikiskill_pairwise_accept_tie_and_regression(self):
        # Synthetic child IDs/output files exercise collection and selection only.
        for verdict,regressions,expected in [('better',[],True),('tie',[],False),('better',['新增关键事实错误'],False)]:
            with self.subTest(verdict=verdict,regressions=regressions):
                root=Path(tempfile.mkdtemp(dir=self.tmp.name))/'study'
                feedback_loop.begin(root,feedback=[{'text':'保留交付状态限定'}])
                h=native_agents.dispatch(root,'codex')['handoffs'][0]
                native_agents.bind(root,h['request_id'],'synthetic-maintainer','codex','fresh')
                fid=feedback_loop.work(root)['feedback'][-1]['id']
                Path(h['result_file']).write_text(json.dumps({'patterns':[{'name':'交付状态','content':'区分预计和实际。','sources':[fid]}]}))
                native_agents.collect(root,h['request_id'])
                h=native_agents.dispatch(root,'codex')['handoffs'][0]
                native_agents.bind(root,h['request_id'],'synthetic-proposer','codex','fresh')
                skill=Path(h['output_directory'])/'SKILL.md';skill.write_text('# 方法\n保留交付状态。')
                Path(h['result_file']).write_text(json.dumps({'skill':str(skill),'note':'测试候选'}))
                native_agents.collect(root,h['request_id'])
                result=feedback_loop.finish(root,pairs=[{'case_id':'synthetic','verdict':verdict,'regressions':regressions}])
                self.assertEqual(result['history'][-1]['accepted'],expected)
                self.assertEqual(result['phase'],'complete')
                if expected:
                    job=self.store.enqueue('learn',{'skill_id':None,'targets':['scout','analyst']})
                    apply_accepted(self.store,job,root,result)
                    self.assertIsNotNone(self.store.meta('active_skill'))
                    self.store.bind_skill(None)
                    apply_accepted(self.store,job,root,result)
                    self.assertIsNone(self.store.meta('active_skill'))


if __name__=='__main__':unittest.main()
