// Reuse the saved contract, not old facts, reviews or runtime authorizations.
export function nextReportUI({$,api,savedVersion,getCurrent,applyRequirements,clearSources,notice,getTemplate,refreshTime=()=>{}}){
 let pending=false,contract=null;
 const form=()=>$('requirements');
 function set(name,value){const field=form()?.elements?.[name];if(!field)return;
  if(field.type==='checkbox')field.checked=!!value;else field.value=value??'';
  field.dispatchEvent?.(new Event('change',{bubbles:true}));
 }
 function clear(){contract=null;set('previous_report_version_id','');set('previous_report_hash','');
  if($('next-report-notice'))$('next-report-notice').hidden=true;
 }
 function requirementsForTemplate(id){return contract&&form()?.elements?.previous_report_version_id?.value&&contract.template_id===id?contract:null}
 async function start(){
  if(pending||!getCurrent())return;
  pending=true;
  try{
   const version=await savedVersion();
   const data=await api('next-report?version_id='+encodeURIComponent(version));
   if(getCurrent()?.id!==version)throw Error('当前报告已切换，请重新选择要复用的版本');
   const candidate=data.requirements;
   if(candidate.template_id){
    const template=getTemplate(candidate.template_id);
    if(!template||template.status!=='ready')throw Error('往期模板已不可用或尚未就绪，请先恢复模板，或手动建立本期需求');
    const sections=template.sections||[];
    if((candidate.sections||[]).some(section=>!sections.some(item=>item.section_id===section.section_id)))
     throw Error('往期模板章节已变化，请核对模板后再复用，避免遗漏人工保留的章节');
   }
   contract=candidate;
   set('completion_mode','standard');
   set('template_id',contract.template_id||'');
   applyRequirements(JSON.stringify(contract));
   for(const key of ['organization','industry','report_date','reader_id','previous_report_version_id','previous_report_hash'])set(key,contract[key]);
   set('allow_web',false);set('fact_check',false);
   clearSources();
   const sections=contract.sections||[];
   for(const row of $('template-sections')?.querySelectorAll('[data-section-id]')||[]){
    const section=sections.find(item=>item.section_id===row.dataset.sectionId);
    if(section){row.querySelector('[data-title]').value=section.title;row.querySelector('select').value=section.mode||'required'}
   }
   const box=$('next-report-notice');
   if(box){box.hidden=false;$('next-report-summary').textContent=`基于《${data.previous.title}》的已保存版本开始下一期。${data.notice}`+
     (data.reader?` 当前读者：${data.reader.name}；用途：${data.reader.decisions}；偏好：${data.reader.preferences}。请确认是否仍适用。`:'');}
   refreshTime();
   form()?.elements?.title?.focus?.();
   notice('已复用约定；请填写本期标题、时间范围并选择材料');
  }finally{pending=false}
 }
 function bind(){const button=$('next-report');if(button)button.onclick=()=>start().catch(error=>notice(error.message,true));
  if($('next-report-cancel'))$('next-report-cancel').onclick=clear;
  form()?.addEventListener?.('reset',clear);
 }
 return {bind,start,clear,requirementsForTemplate};
}
