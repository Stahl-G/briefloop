// Explicit per-version observations, independent of scoring and formal review.
const labels={supported_for_scope:'候选：证据支持',contradicted:'候选：证据矛盾',insufficient_evidence:'候选：证据不足',unknown:'候选：无法判断',not_checked:'尚未核查'};

export function observationHTML(record,esc){
 const result=record.result||{};
 return `<article class="issue-card"><strong>${esc({queued:'已排队',running:'预检进行中',complete:'预检请求已收尾',failed:'预检未完成',interrupted:'预检已中断',cancelled:'预检已停止'}[record.status]||record.status)}</strong>${record.stale?'<p class="help">来源或检查协议已变化；以下是旧输入的观察，本次不能沿用。</p>':''}${record.error?`<p class="help">${esc(record.error)}</p>`:''}${(result.items||[]).map(item=>`<details class="finding"><summary>${esc(labels[item.status]||'尚未核查')} · ${esc(item.statement)}</summary>${item.reason?`<p>${esc(item.reason)}</p>`:''}${item.actual_model?`<p class="help">实际模型：${esc(item.actual_model)}</p>`:''}${item.probabilities?`<p class="help">模型返回的概率分布（未经本任务校准，不是真实性概率）：${Object.entries(item.probabilities).map(([key,value])=>`${esc(labels[key]||key)} ${esc((value*100).toFixed(1))}%`).join(' · ')}</p>`:''}</details>`).join('')}<p class="help">Jev 观察不改写正文、不替代独立审阅，也不改变正式交付状态。</p></article>`;
}

export function createJevChecks({$,api,action,notice,esc,getCurrent,isDirty,savedVersion,confirm=message=>window.confirm(message)}){
 let ticket=0,signature='',busy=false;
 async function render(){
  const current=getCurrent(),box=$('jev-checks');if(!current||!box)return;
  const vid=current.id,flight=++ticket;
  if(box.dataset.version!==vid){box.dataset.version=vid;box.replaceChildren();signature=''}
  // Loading this section is itself intentional; ordinary polling never reads
  // provider settings or builds every report's evidence preview in the background.
  if(!box.firstChild){
   box.innerHTML='<details data-testid="jev-checks"><summary>Jev 依据预检 · 可选</summary><div data-jev-content></div></details>';
   box.firstChild.addEventListener('toggle',()=>{if(box.firstChild.open)refresh()});
  }
  if(box.firstChild.open)await refresh(flight);
 }
 async function refresh(parentTicket){
  const box=$('jev-checks'),vid=getCurrent()?.id;if(!box?.firstChild?.open||!vid)return;
  const flight=parentTicket??++ticket;
  let data;try{data=await api('jev-checks?version='+encodeURIComponent(vid))}catch{
   if(getCurrent()?.id===vid&&flight===ticket)box.querySelector('[data-jev-content]').textContent='暂时无法读取预检范围，请重新展开。';return;
  }
  if(getCurrent()?.id!==vid||flight!==ticket||!box.firstChild?.open)return;
  const content=box.querySelector('[data-jev-content]'),next=JSON.stringify([vid,data,isDirty()]);
  // Polling must not destroy a key being typed or reset open evidence details.
  const editingKey=document.activeElement?.matches?.('[data-jev-key]')&&document.activeElement.value;
  if(signature===next||editingKey)return;
  signature=next;
  const eligible=data.items.filter(item=>item.eligible).length;
  const latest=data.records.find(record=>!record.stale),active=latest&&['queued','running'].includes(latest.status);
  content.innerHTML=`<p class="help">${esc(data.scope)}</p><p class="help">将向 TypeSafe（api.typesafe.ai）发送下列 ${eligible} 条结论及摘录上下文，使用你的 Jev 额度。只有点击开始后才外发。</p><p class="help">未绑定具体正文的引用 ${data.unbound_citations} 条；它们没有进入预检，也不代表正文其余结论已核查。</p><details><summary>查看将发送的材料</summary>${data.items.map(item=>`<div class="finding"><blockquote>${esc(item.statement)}</blockquote>${!item.eligible?`<p class="help">不发送：${esc(item.unavailable.join('；')||'缺少可定位依据')}</p>`:item.evidence.map(source=>`<p><strong>${esc(source.title)}</strong> · ${esc(source.context_locator)}</p><blockquote>${esc(source.source_context)}</blockquote>`).join('')}</div>`).join('')||'<p class="help">请先完成当前稿件的补依据。</p>'}</details><details><summary>配置 Jev · ${data.provider.configured?'已保存配置':'尚未配置'}</summary><label>TypeSafe API Key<input type="password" data-jev-key autocomplete="off"></label><button type="button" data-jev-save>保存到本机</button>${data.provider.source==='file'?'<button type="button" data-jev-remove>移除本机密钥</button>':''}<p class="help">密钥不回显、不放入工作区或模型材料；保存不会调用服务。环境变量 TYPESAFE_API_KEY 优先。</p></details>${isDirty()?'<p class="help">有未保存编辑，保存后重新查看本稿范围。</p>':''}<button type="button" class="outline" data-jev-start ${busy||active||!eligible||!data.provider.configured||isDirty()||latest?'disabled':''}>${active?'Jev 正在预检':latest?'本次输入已有预检记录':'开始 Jev 预检'}</button>${data.records.map(record=>observationHTML(record,esc)).join('')}`;
  content.querySelector('[data-jev-save]').onclick=()=>action(async()=>{
   const input=content.querySelector('[data-jev-key]'),key=input.value.trim();if(!key)return;
   try{await api('jev',{api_key:key});notice('Jev 密钥已保存，未发起外部调用')}finally{input.value='';signature='';await refresh()}
  });
  content.querySelector('[data-jev-remove]')?.addEventListener('click',()=>action(async()=>{await api('jev',{remove:true});signature='';await refresh()}));
  content.querySelector('[data-jev-start]').onclick=()=>action(async()=>{
   if(busy||getCurrent()?.id!==vid||isDirty())return;
   if(!confirm(`将这 ${eligible} 条结论及已展示的摘录发送至 TypeSafe，使用 Jev 额度进行预检？结果不会替代独立审阅。`))return;
   busy=true;content.querySelector('[data-jev-start]').disabled=true;
   try{
    const version=await savedVersion();if(version!==vid)return;
    await api('jev-checks',{version_id:vid,fingerprint:data.fingerprint,allow_external:true});
    notice('Jev 预检已排队，结果绑定当前已保存版本');
   }finally{busy=false;signature='';await refresh()}
  });
 }
 return {render};
}
