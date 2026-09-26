import {test} from 'node:test';
import assert from 'node:assert/strict';
import {jobExecutionLabel,jobResumeLabel,createLocalFileProgress} from '../frontend/job-presentation.js';

test('file tasks display local work and retry without a model identity',async()=>{
 const job={id:'xlsx-job',kind:'export_xlsx',status:'failed',payload:'{}',error:'<failed>'};
 const model=()=>{throw Error('file task must not inspect model')};
 assert.equal(jobExecutionLabel(job,model),'本地文件处理');
 assert.equal(jobResumeLabel(job),'重新制作文件');
 const routes=[],actions={};
 const element={hidden:true,innerHTML:'',querySelector:selector=>({addEventListener:(event,handler)=>actions[selector]=handler})};
 const api=async(route,body)=>{routes.push([route,body]);return [{kind:'export_progress',data:JSON.stringify({message:'生成工作表'})}]};
 const ui=createLocalFileProgress({element,api,action:f=>f(),taskLabel:()=> '生成报表 Excel'});
 await ui.render(job,()=>true);
 assert.match(element.innerHTML,/文件制作未完成/);assert.match(element.innerHTML,/&lt;failed&gt;/);
 assert.doesNotMatch(element.innerHTML,/模型进程|沿用原模型|修改模型/);
 await actions['[data-file-resume]']();
 assert.deepEqual(routes,[['events?job=xlsx-job',undefined],['resume',{job_id:'xlsx-job'}]]);
 element.innerHTML='newer report';await ui.render(job,()=>false);assert.equal(element.innerHTML,'newer report');
 assert.equal(jobExecutionLabel({kind:'generate',payload:'{"runtime":{"model":"frozen"}}'},()=> 'frozen'),'frozen');
 assert.equal(jobResumeLabel({kind:'generate'}),'沿用原模型恢复');
});
