import {$,esc} from './dom.js';
import {inZone} from './time.js';
export function scheduleUI({api,getState,refresh,notice,openReport}){ const display=(s,zone)=>s?inZone(s,zone):'—';
 let editing=null,requirements={};
 const statuses={accepted:'已排队',queued:'已排队',running:'执行中',complete:'已完成',failed:'失败',interrupted:'已中断',cancelled:'已停止',skipped:'已跳过'};
 function render(){
  const rows=getState()?.schedules||[];
  const block=document.getElementById('home-block-schedule');
  if(block)block.hidden=!rows.length;
  if(!rows.length){const list=$('schedule-list');if(list)list.innerHTML='';return}
  $('schedule-list').innerHTML=rows.map(row=>{const c=row.config,h=row.history?.[0],latestReport=row.history?.find(item=>item.version_id),freq=c.frequency==='custom'?`每 ${c.every} ${ {minutes:'分钟',hours:'小时',days:'天',weeks:'周'}[c.unit]}`:{daily:'每天',weekly:'每周',monthly:'每月'}[c.frequency];return `<article class="panel schedule-card"><strong>${esc(row.name)}</strong><span class="chip">${row.enabled?'已启用':'已暂停'}</span><p>${esc(freq)} · ${esc(c.timezone)}<br>下次：${row.enabled?esc(display(row.next_at,c.timezone)):'暂停中'}</p>${h?`<p class="help">最近：${esc(statuses[h.job_status||h.status]||h.status)}${h.error||h.job_error||h.runtime_message?' · '+esc(h.error||h.job_error||h.runtime_message):''}</p>`:''}<div class="schedule-actions"><button data-schedule-edit="${esc(row.id)}">编辑</button><button data-schedule-toggle="${esc(row.id)}">${row.enabled?'暂停':'启用'}</button><button data-schedule-run="${esc(row.id)}">立即运行</button>${row.history?.some(item=>['queued','running'].includes(item.job_status))?`<button data-schedule-stop="${esc(row.history.find(item=>['queued','running'].includes(item.job_status)).job_id)}">停止本次</button>`:''}${latestReport?`<button data-schedule-report="${esc(latestReport.version_id)}">查看报告</button>`:''}<button data-schedule-delete="${esc(row.id)}">删除</button></div></article>`}).join('');
 }
 function open(id){
  const state=getState(),row=(state.schedules||[]).find(s=>s.id===id);editing=row||null;
  requirements=structuredClone(row?.config.requirements||state.requirements||{});
  const config=row?.config||{},later=new Date(Date.now()+3600000);later.setSeconds(0,0);
  const local=new Date(later.getTime()-later.getTimezoneOffset()*60000).toISOString().slice(0,16);
  $('schedule-name').value=row?.name||requirements.title||'';$('schedule-title').value=requirements.title||'';$('schedule-objective').value=requirements.objective||'';
  $('schedule-frequency').value=config.frequency||'weekly';$('schedule-start').value=config.start||local;$('schedule-timezone').value=config.timezone||Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC';
  $('schedule-every').value=config.every||1;$('schedule-unit').value=config.unit||'hours';$('schedule-web').checked=!!requirements.allow_web;
  $('schedule-sources').innerHTML=(state.sources||[]).map(s=>`<label><input type="checkbox" value="${esc(s.id)}" ${config.source_ids?.includes(s.id)?'checked':''}>${esc(s.name)}</label>`).join('')||'<span class="help">暂无材料，可先添加材料或启用联网。</span>';
  $('schedule-preview').textContent='';$('schedule-error').textContent='';custom();$('schedule-dialog').showModal();
 }
 function custom(){$('schedule-custom').hidden=$('schedule-frequency').value!=='custom';}
 function body(){return {id:editing?.id,name:$('schedule-name').value,enabled:editing?.enabled??true,config:{frequency:$('schedule-frequency').value,start:$('schedule-start').value,timezone:$('schedule-timezone').value,every:Number($('schedule-every').value),unit:$('schedule-unit').value,requirements:{...requirements,title:$('schedule-title').value,objective:$('schedule-objective').value,allow_web:$('schedule-web').checked},source_ids:[...$('schedule-sources').querySelectorAll('input:checked')].map(n=>n.value)}};}
 $('schedule-new').onclick=()=>open();$('schedule-cancel').onclick=()=>$('schedule-dialog').close();$('schedule-frequency').onchange=custom;
 $('schedule-preview-button').onclick=async()=>{try{const data=body(),result=await api('schedules/preview',data);$('schedule-preview').textContent='下次执行：'+display(result.next_at,data.config.timezone);$('schedule-error').textContent='';}catch(e){$('schedule-error').textContent=e.message;}};
 $('schedule-form').onsubmit=async e=>{e.preventDefault();const submit=e.submitter;submit.disabled=true;try{await api('schedules/save',body());$('schedule-dialog').close();await refresh();notice('定时报告已保存');}catch(err){$('schedule-error').textContent=err.message;}finally{submit.disabled=false;}};
 $('schedule-list').onclick=async e=>{const b=e.target.closest('button');if(!b)return;try{if(b.dataset.scheduleEdit){open(b.dataset.scheduleEdit);return;}if(b.dataset.scheduleReport){openReport(b.dataset.scheduleReport);return;}b.disabled=true;let result;if(b.dataset.scheduleStop)result=await api('stop',{job_id:b.dataset.scheduleStop});else if(b.dataset.scheduleRun)result=await api('schedules/run',{id:b.dataset.scheduleRun,request_id:crypto.randomUUID()});else {const id=b.dataset.scheduleToggle||b.dataset.scheduleDelete,action=b.dataset.scheduleDelete?'delete':'toggle';if(action==='delete'&&!confirm('删除此计划？已经生成或执行中的报告会保留。'))return;result=await api('schedules/change',{id,action});}await refresh();if(result.error)notice(result.error,true);else if(b.dataset.scheduleRun)notice('报告已加入任务队列');}catch(err){notice(err.message,true);}finally{b.disabled=false;}};
 return {render};
}
