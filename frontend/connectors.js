// Settings UI uses public connector DTOs only. Existing credentials never enter
// the browser; blank credential inputs preserve the server-side binding.
export function connectorSettings(root, api) {
 const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const labels={disabled:'未启用',disconnected:'未连接',connecting:'连接中',connected:'已连接',error:'连接异常'};
 let records=[],editing=null,busy=false;
 root.innerHTML=`<div class="section-title"><h2>数据连接器（MCP）</h2><button type="button" data-refresh>刷新状态</button></div>
 <p class="help">管理本工作区的 MCP 服务与连接状态。连接测试读取服务能力目录。</p>
 <p data-status role="status" aria-live="polite"></p><div data-list class="connector-list"></div>
 <button type="button" data-new class="outline">＋ 添加连接器</button>
 <form data-form class="connector-form" hidden><h3 data-form-title>添加连接器</h3>
 <label>名称<input name="name" maxlength="160" required placeholder="例如：团队资料库"></label>
 <label>连接方式<select name="transport"><option value="http">HTTP 服务</option><option value="stdio">本机程序（stdio）</option></select></label>
 <div data-http><label>MCP 服务地址<input name="url" type="url" placeholder="https://example.com/mcp"></label>
 <label>访问令牌（可选）<input name="bearer_token" type="password" autocomplete="new-password" placeholder="留空保留已保存令牌"></label></div>
 <div data-stdio hidden><label>程序路径<input name="command" placeholder="已安装程序的绝对路径"></label>
 <label>参数（每行一个）<textarea name="args" rows="3" placeholder="每行作为一个参数，不需要加引号"></textarea></label>
 <label>工作目录（可选）<input name="cwd" placeholder="目录的绝对路径"></label>
 <label>环境变量（可选，每行 NAME=VALUE）<textarea name="env" rows="2" autocomplete="off" spellcheck="false" placeholder="留空保留已保存环境变量"></textarea></label></div>
 <p data-credential-note class="help"></p><label class="check"><input type="checkbox" name="clear_credentials">清除已保存的连接凭据</label>
 <details><summary>连接选项</summary><div class="row"><label>请求等待上限（秒）<input name="timeout_seconds" type="number" min="0.1" max="120" step="0.1" value="20" required></label>
 <label>单次返回上限（MB）<input name="max_response_mb" type="number" min="0.001" max="8" step="0.001" value="1" required></label></div></details>
 <p class="help">保存配置不会运行程序或连接服务。修改后需重新测试或启用。</p>
 <div class="connector-actions"><button class="primary" type="submit">保存配置</button><button type="button" data-cancel>取消</button></div></form>`;
 const find=selector=>root.querySelector(selector),form=find('[data-form]'),field=name=>form.elements[name];
 function transport(){const http=field('transport').value==='http';find('[data-http]').hidden=!http;find('[data-stdio]').hidden=http;field('url').required=http;field('command').required=!http}
 function edit(record=null){
  editing=record?.id||null;form.reset();form.hidden=false;
  find('[data-form-title]').textContent=record?'修改连接器':'添加连接器';
  for(const name of ['name','transport','url','command','cwd','timeout_seconds'])if(record?.[name]!=null)field(name).value=record[name];
  field('args').value=(record?.args||[]).join('\n');
  if(record)field('max_response_mb').value=record.max_response_bytes/1048576;
  find('[data-credential-note]').textContent=record?.has_credentials?'本机已保存凭据。留空保留；填写时替换该连接的凭据。'+(record.env_names?.length?' 环境变量：'+record.env_names.join('、'):''):'凭据只保存在本机，不回显已保存的值。';
  transport();field('name').focus();
 }
 function render(){
  find('[data-list]').innerHTML=records.length?records.map(r=>{
   const caps=r.capabilities||{},test=r.last_test;
   return `<article class="connector-card"><div class="section-title"><h3>${esc(r.name)}</h3><span class="tag ${r.state==='error'?'error':''}">${esc(labels[r.state]||r.state)}</span></div>
    <p class="help connector-address">${esc(r.transport==='http'?r.url:r.command)} · v${esc(r.revision)}</p>
    ${r.protocol?`<p class="help">协议 ${esc(r.protocol)} · ${caps.tools?.length||0} 个工具 · ${caps.resources?.length||0} 个资源</p>`:''}
    ${test?`<p class="help">最近测试${test.ok?'通过':'未通过'} · ${esc(new Date(test.checked_at).toLocaleString())}${test.duration_seconds!=null?' · '+Number(test.duration_seconds).toFixed(1)+' 秒':''}</p>`:''}
    ${r.error||test?.error?`<p class="connector-error">${esc((r.error||test.error).message)}</p>`:''}
    <div class="connector-actions"><button type="button" data-op="test" data-id="${esc(r.id)}">测试连接</button>
    ${r.enabled&&r.state!=='connected'?`<button type="button" data-op="enable" data-id="${esc(r.id)}">重新连接</button>`:''}
    <button type="button" data-op="${r.enabled?'disable':'enable'}" data-id="${esc(r.id)}">${r.enabled?'停用':'启用连接'}</button>
    <button type="button" data-edit="${esc(r.id)}">修改</button><button type="button" data-delete="${esc(r.id)}">移除</button></div>
    <div data-remove="${esc(r.id)}" class="connector-remove" hidden><p>移除此连接配置和本机保存的连接凭据？已有报告与来源保留。</p><button type="button" data-op="delete" data-id="${esc(r.id)}">确认移除</button><button type="button" data-keep>保留</button></div></article>`;
  }).join(''):'<p class="help">还没有连接器。添加一个 MCP 服务后即可测试连接。</p>';
  root.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>edit(records.find(r=>r.id===b.dataset.edit)));
  root.querySelectorAll('[data-delete]').forEach(b=>b.onclick=()=>{b.closest('article').querySelector('[data-remove]').hidden=false});
  root.querySelectorAll('[data-keep]').forEach(b=>b.onclick=()=>{b.parentElement.hidden=true});
  root.querySelectorAll('[data-op]').forEach(b=>b.onclick=()=>run(async()=>{
   const result=await api('connectors/'+b.dataset.op,{connector_id:b.dataset.id});
   await load();
   if(b.dataset.op==='test')return result.ok?'连接测试通过。':result.error?.message||'连接测试未通过。';
   if(result.error)return result.error.message;
   if(b.dataset.op==='delete'&&editing===b.dataset.id){form.reset();form.hidden=true;editing=null}
   return {enable:'连接已启用。',disable:'连接已停用。',delete:'连接配置已移除。'}[b.dataset.op];
  },b.dataset.op==='test'?'正在连接并读取能力目录…':'正在处理连接…'));
 }
 async function load(){const result=await api('connectors');records=result.connectors;render()}
 async function run(operation,message='正在保存…'){
  if(busy)return;busy=true;find('[data-status]').textContent=message;
  root.querySelectorAll('button').forEach(b=>b.disabled=true);
  try{const message=await operation();find('[data-status]').textContent=message||''}
  catch(e){find('[data-status]').textContent=e.message||'操作未完成，请重试。'}
  finally{busy=false;root.querySelectorAll('button').forEach(b=>b.disabled=false)}
 }
 form.onsubmit=event=>{event.preventDefault();run(async()=>{
  const http=field('transport').value==='http';
  const config={name:field('name').value,transport:field('transport').value,timeout_seconds:Number(field('timeout_seconds').value),max_response_bytes:Math.round(Number(field('max_response_mb').value)*1048576)};
  if(http)config.url=field('url').value.trim();
  else Object.assign(config,{command:field('command').value.trim(),args:field('args').value.split('\n').filter(s=>s!==''),cwd:field('cwd').value.trim()||null});
  const body={config,connector_id:editing};
  if(field('clear_credentials').checked)body.secrets={};
  else if(http&&field('bearer_token').value)body.secrets={bearer_token:field('bearer_token').value};
  else if(!http&&field('env').value.trim()){
   const env={};for(const line of field('env').value.split('\n').filter(s=>s.trim())){const i=line.indexOf('=');if(i<1)throw Error('每行环境变量请使用 NAME=VALUE 格式。');env[line.slice(0,i).trim()]=line.slice(i+1)}body.secrets={env};
  }
  await api('connectors/save',body);form.reset();form.hidden=true;editing=null;await load();return '配置已保存，可测试或启用连接。';
 })};
 field('transport').onchange=transport;
 find('[data-new]').onclick=()=>edit();find('[data-cancel]').onclick=()=>{form.reset();form.hidden=true;editing=null};
 find('[data-refresh]').onclick=()=>run(load,'正在读取连接状态…');
 return {refresh:()=>run(load,'正在读取连接状态…')};
}
