import {esc} from './dom.js';
import {runtimeIcon} from './runtime-cards.js';

const preferred=['codex','claude','opencode','pi','deepseek-harness','antigravity'];
export function welcomeAgents(catalog,chosen,expanded=false){
 const agents=catalog.filter(r=>r.available&&r.id!=='briefloop-native');
 const rank=id=>{const index=preferred.indexOf(id);return index<0?preferred.length:index};
 agents.sort((a,b)=>rank(a.id)-rank(b.id));
 const visible=expanded?agents:agents.slice(0,6);
 const selected=agents.find(r=>r.id===chosen);
 if(!expanded&&selected&&!visible.includes(selected))visible[visible.length-1]=selected;
 return {visible,total:agents.length};
}
export function welcomeAgentCard(runtime,chosen,busy=false){
 const selected=runtime.id===chosen;
 return `<button type="button" class="welcome-agent${selected?' selected':''}" data-testid="welcome-agent-${esc(runtime.id)}" data-welcome-agent="${esc(runtime.id)}" aria-pressed="${selected}" ${busy?'disabled':''}><span class="runtime-icon" aria-hidden="true">${runtimeIcon(runtime.id)}</span><span class="welcome-agent-text"><strong>${esc(runtime.name||runtime.id)}</strong><small>已检测到</small></span><span class="welcome-agent-check" aria-hidden="true">${selected?'✓':''}</span></button>`;
}
export function welcomeReady(settings,catalog,mode,busy=false){
 return !busy&&!settings.model_selection_required&&!!settings.model
  &&(mode==='native')===(settings.agent_backend==='briefloop-native')
  &&catalog.some(r=>r.id===settings.agent_backend&&r.available);
}
