// Per-report selection, independent of the workspace connector enable switch.
export function materialSelection(records, chosen, maxCalls, maxBytes) {
 const selections=[];
 for(const [id,items] of chosen){if(!items.resources.size&&!items.tools.size)continue;const row=records.find(r=>r.id===id);if(!row?.enabled||row.state!=='connected'||[...items.resources].some(uri=>!row.capabilities?.resources?.some(r=>r.uri===uri))||[...items.tools].some(name=>!row.capabilities?.tools?.some(t=>t.name===name)))throw Error('已选连接器或材料不再可用，请刷新并重新选择');}
 for(const row of records){
  if(!row.enabled||row.state!=='connected')continue;
  const items=chosen.get(row.id);if(!items)continue;
  const resources=(row.capabilities?.resources||[]).map(r=>r.uri).filter(uri=>items.resources.has(uri));
  const tools=(row.capabilities?.tools||[]).map(t=>t.name).filter(name=>items.tools.has(name));
  if(resources.length||tools.length)selections.push({connector_id:row.id,resources,tools});
 }
 if(!selections.length)return null;
 if(!Number.isInteger(maxCalls)||maxCalls<1||maxCalls>1000)throw Error('连接器调用次数须为 1–1000 的整数');
 if(!Number.isInteger(maxBytes)||maxBytes<1||maxBytes>1073741824)throw Error('连接器材料预算须为 1–1024 MiB');
 const minimum=Math.max(...records.filter(r=>selections.some(s=>s.connector_id===r.id)).map(r=>r.max_response_bytes||0));
 if(maxBytes<minimum)throw Error('材料预算不能小于所选连接器的单次响应上限');
 return {selections,max_calls:maxCalls,max_total_bytes:maxBytes};
}
export function mcpSelection(root,api){
 const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const chosen=new Map();let records=[],loading=false,loadError=false;
 root.innerHTML=`<details><summary>本轮连接器材料（可选）</summary><p class="help">选择允许 BriefLoop 读取的材料和调用的工具。联网开关单独控制公开搜索。</p><button type="button" class="outline" data-refresh>刷新连接器</button><p data-status role="status"></p><div data-catalog></div><div class="row"><label>调用次数上限<input type="number" data-calls min="1" max="1000" step="1" value="20"></label><label>材料总量上限（MiB）<input type="number" data-bytes min="1" max="1024" step="1" value="20"></label></div><p class="help">仅勾选项授予本轮任务；未勾选时不使用连接器。读取成功的材料会出现在报告来源中。</p></details>`;
 const find=s=>root.querySelector(s);
 function render(){
  const rows=records.filter(r=>r.enabled);
  find('[data-catalog]').innerHTML=rows.map(r=>{const selected=chosen.get(r.id),caps=r.capabilities||{},connected=r.state==='connected';return `<fieldset><legend>${esc(r.name||r.id)} · ${connected?'已连接':'尚未连接'}</legend>${!connected?'<p class="help">请先在设置中启用并连接，再刷新。</p>':''}${(caps.resources||[]).map(item=>`<label class="check"><input type="checkbox" data-connector="${esc(r.id)}" data-kind="resources" data-item="${esc(item.uri)}" ${selected?.resources.has(item.uri)?'checked':''}>${esc(item.name||item.uri)}</label>`).join('')}${(caps.tools||[]).map(item=>`<label class="check"><input type="checkbox" data-connector="${esc(r.id)}" data-kind="tools" data-item="${esc(item.name)}" ${selected?.tools.has(item.name)?'checked':''}>${esc(item.title||item.name)}${item.annotations?.readOnlyHint===true?'（声明只读）':'（可能修改外部数据，请确认用途）'}</label>`).join('')}${connected&&!caps.resources?.length&&!caps.tools?.length?'<p class="help">未提供可选材料或工具。</p>':''}</fieldset>`}).join('')||'<p class="help">没有已启用的连接器。可在设置 → 数据连接器中添加。</p>';
  root.querySelectorAll('[data-item]').forEach(el=>el.onchange=()=>{const {connector,kind,item}=el.dataset;if(!chosen.has(connector))chosen.set(connector,{resources:new Set(),tools:new Set()});const values=chosen.get(connector)[kind];el.checked?values.add(item):values.delete(item)});
 }
 async function refresh(){if(loading)return;loading=true;find('[data-refresh]').disabled=true;find('[data-status]').textContent='正在读取连接器…';try{const result=await api('connectors');records=result.connectors||[];loadError=false;render();find('[data-status]').textContent='';}catch(e){loadError=true;find('[data-status]').textContent='无法读取连接器：'+e.message;}finally{loading=false;find('[data-refresh]').disabled=false}}
 find('[data-refresh]').onclick=refresh;
 return {refresh,selection(){if(loading)throw Error('连接器目录正在刷新，请稍后提交');if(loadError&&[...chosen.values()].some(v=>v.resources.size||v.tools.size))throw Error('连接器目录读取失败，请刷新后提交');return materialSelection(records,chosen,Number(find('[data-calls]').value),Number(find('[data-bytes]').value)*1048576)}};
}
