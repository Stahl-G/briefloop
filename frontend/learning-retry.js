// A linked retry can confirm a new budget while leaving the old batch intact.
export function learningRetry({api,getPlan,confirm,describePlan}){
 return async job=>{
  const body={job_id:job.id,use_current_model:true};
  if(job.kind==='learn'){
   const plan=getPlan();if(!plan||!confirm('按当前模型新建学习尝试，保留原任务与反馈。\n\n'+describePlan(plan)))return;
   body.confirm_plan=plan.fingerprint;
  }
  return api('resume',body);
 };
}
