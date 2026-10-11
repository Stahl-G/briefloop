// Report choices belong to this conversation's next request. Workspace learning
// and runtime permissions keep their own existing settings and handlers.
export function createComposerOptions({$,getChat,getState,availability,rememberDraft,openSettings,openReview,document:doc=globalThis.document}){
 let panel=null,root=null;
 function read(){
  const saved=getChat().reportOptions||{},state=getState();
  return {completion_mode:saved.completion_mode||'standard',research_tier:saved.research_tier||state?.settings?.research_tier||'standard',research_strategy:saved.research_strategy||'guided',fact_check:saved.fact_check??!!state?.settings?.fact_checker};
 }
 function sync(){
  if(!root||!getState()?.settings)return;
  const choice=read(),allowed=choice.completion_mode==='fast'?{enabled:false,target:'standard',reason:'快速稿使用后台普通评价；额外联网事实核查需要标准模式。'}:availability(),web=$('chat-allow-web');
  root.querySelectorAll('[data-report-source]').forEach(button=>button.setAttribute('aria-pressed',String((button.dataset.reportSource==='web')===web.checked)));
  root.querySelector('[data-completion]').value=choice.completion_mode;
  root.querySelector('[data-research-tier]').value=choice.research_tier;
  const strategy=root.querySelector('[data-research-strategy]');strategy.value=choice.research_strategy;strategy.disabled=['fast','direct'].includes(choice.completion_mode);
  const fact=root.querySelector('[data-report-fact]');fact.checked=choice.fact_check&&allowed.enabled;fact.disabled=!allowed.enabled;
  const note=root.querySelector('[data-fact-link]');note.hidden=allowed.enabled;note.textContent=allowed.target==='standard'?'标准模式可开启':allowed.target==='review'?'选择审阅连接':allowed.target==='network'?'配置联网核查':'查看核查设置';note.title=allowed.reason||'';
  root.querySelector('[data-report-mode-note]').textContent=choice.completion_mode==='fast'?'先保存可编辑初稿，再补充依据与评价。':choice.completion_mode==='direct'?'一个作者在同一上下文中检索和写作，保存后再定位依据、独立评价和修订。':'按研究、写作和检查流程完成报告。';
 }
 function instruction(){
  const choice=read();choice.fact_check=choice.completion_mode!=='fast'&&choice.fact_check&&availability().enabled;
  // Fast web is a separate product mode; existing material mode never silently searches.
  if(choice.completion_mode==='fast'&&$('chat-allow-web').checked)choice.completion_mode='fast_web';
  return `\n\n本轮报告选项（仅当用户要求生成报告时使用，不因此自动生成）：completion_mode=${choice.completion_mode}，research_tier=${choice.research_tier}，research_strategy=${choice.research_strategy}，fact_check=${choice.fact_check}。生成时传入 requirements；用户正文另有明确选择则按正文。`;
 }
 function mount(target){
  panel=target;panel.classList.add('composer-research-panel');$('composer-params').textContent='研究选项';
  const runtimeGrid=panel.querySelector('.composer-params-grid'),actions=panel.querySelector('.composer-params-row');
  // Legacy controls remain script carriers; model choices now live in their own dialog.
  // Keep speed and permission actions accessible outside research settings.
  const carriers=doc.createElement('div');carriers.id='chat-runtime-carriers';carriers.hidden=true;
  const speed=$('chat-service-tier');if(speed){const label=doc.createElement('label');label.dataset.fastControl='';label.textContent='当前对话速度';label.hidden=speed.hidden;label.append(speed);$('settings-model-block').append(label)}
  if(runtimeGrid)carriers.append(runtimeGrid);$('chat-form').append(carriers);
  const permission=$('chat-permissions-open');if(permission)$('composer-params').parentElement.before(permission);
  root=doc.createElement('section');root.className='composer-report-settings';
  root.innerHTML='<h3>本次报告</h3><div class="composer-report-field"><span>来源范围</span><div class="composer-choice-group"><button type="button" data-report-source="existing">已有来源</button><button type="button" data-report-source="web">允许补搜</button></div></div><label class="composer-report-field"><span>生成方式</span><select data-completion aria-label="本次报告生成方式"><option value="fast">快速</option><option value="standard">标准</option><option value="direct">直写（试用）</option></select></label><p class="help" data-report-mode-note></p><label class="composer-report-field"><span>研究方法</span><select data-research-strategy aria-label="本次报告研究方法"><option value="guided">分轮研究</option><option value="goal_driven">按目标补证（试用）</option></select></label><p class="help">按目标补证由重要问题和证据缺口决定下一步；预算与权限不变。用于标准生成，尚未证明质量优于分轮研究。</p><details class="composer-report-advanced"><summary>高级选项</summary><label class="composer-report-field"><span>研究深度</span><select data-research-tier aria-label="本次报告研究深度"><option value="quick">快速</option><option value="standard">标准</option><option value="deep">深入</option></select></label><div class="composer-report-fact"><label><input type="checkbox" data-report-fact>事实核查</label><button type="button" class="subtle-button" data-fact-link></button></div></details><p class="composer-report-scope">仅用于本次报告，不改动工作区默认设置。</p>';
  panel.append(root);if(actions)root.append(actions);
  root.querySelectorAll('[data-report-source]').forEach(button=>{button.onclick=()=>{webChange(button.dataset.reportSource==='web');sync()}});
  root.querySelector('[data-completion]').onchange=event=>change('completion_mode',event.target.value);
  root.querySelector('[data-research-strategy]').onchange=event=>change('research_strategy',event.target.value);
  root.querySelector('[data-research-tier]').onchange=event=>change('research_tier',event.target.value);
  root.querySelector('[data-report-fact]').onchange=event=>change('fact_check',event.target.checked);
  root.querySelector('[data-fact-link]').onclick=()=>{if(read().completion_mode==='fast'){change('completion_mode','standard');return}close();if(availability().target==='review')openReview();else openSettings('execution')};
  sync();
 }
 function webChange(value){$('chat-allow-web').checked=value;$('chat-allow-web').dispatchEvent(new Event('change',{bubbles:true}))}
 function change(field,value){getChat().reportOptions={...read(),[field]:value};rememberDraft();sync()}
 function close(){if(panel)panel.hidden=true;$('composer-params').setAttribute('aria-expanded','false')}
 return {mount,sync,read,instruction};
}
