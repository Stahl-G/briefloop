// Runtime values and their order come from that executable's own --help.
// No translated aliases or inferred mode names are added to native directories.
function optionBlock(help:string,flag:string):string{
 const lines=help.replace(/\x1b\[[0-9;]*m/g,'').split(/\r?\n/);
 const escaped=flag.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
 const start=lines.findIndex(line=>new RegExp('(?:^|\\s|,)'+escaped+'(?=\\s|=|,|$)').test(line));
 if(start<0)return '';
 let end=start+1;
 while(end<lines.length&&!/^\s*(?:-\w,?\s+)?--[A-Za-z]/.test(lines[end])&&!/^(?:Commands|Options|Examples|Available subcommands|Slash Commands):/.test(lines[end]))end++;
 return lines.slice(start,end).join('\n');
}
function values(raw:string):string[]{
 return [...new Set(raw.split(/\s*,\s*(?:or\s+)?|\s+or\s+|\|/).map(value=>value.trim().replace(/^["']|["']$/g,'')).filter(value=>/^[A-Za-z][A-Za-z0-9-]*$/.test(value)))];
}
function nativeModes(ids:string[],runtime:string){
 return ids.map(id=>({id,name:id,native_name:id,description:'',
  ...(/bypass|yolo|danger-full-access/i.test(id)?{advanced:true}:{}),
  ...((runtime==='claude'&&/bypass/i.test(id)||runtime==='codex'&&id==='danger-full-access')?{disabled:true,disabled_reason:'当前 BriefLoop 适配器不支持此权限范围'}:{})}));
}
export function unavailablePermissions(runtime:string,diagnostic:string){
 return {modes:[],source:{kind:'unavailable',label:runtime},default_mode:'native',auto_available:false,diagnostic};
}
export function permissionsFromHelp(runtime:string,help:string):any{
 let ids:string[]=[],flag='',block='',nativeDefault:string|undefined;
 if(runtime==='claude'){
  flag='--permission-mode';block=optionBlock(help,flag);
  const choice=/\(choices:\s*([\s\S]*?)\)/.exec(block);
  if(choice)ids=[...choice[1].matchAll(/["']([^"']+)["']/g)].map(match=>match[1]);
 }else if(runtime==='zcode'){
  flag='--mode';block=optionBlock(help,flag);
  const choice=/Permission mode for prompts:\s*([^\n(]+)/i.exec(block);
  if(choice)ids=values(choice[1]);
  nativeDefault=/\(default:\s*([A-Za-z][\w-]*)/i.exec(block)?.[1];
 }else if(runtime==='antigravity'){
  flag='--mode';block=optionBlock(help,flag);
  const choice=/execution mode[^\n]*?\(([^)]+)\)/i.exec(block);
  if(choice)ids=values(choice[1]);
 }else if(runtime==='codex'){
  flag='--sandbox';block=optionBlock(help,flag);
  const choice=/\[possible values:\s*([^\]]+)\]/i.exec(block);
  if(choice)ids=values(choice[1]);
 }else if(runtime==='pi'){
  const tools=optionBlock(help,'--tools'),none=optionBlock(help,'--no-tools'),extensions=optionBlock(help,'--no-extensions');
  if(!tools||!none||!extensions)return unavailablePermissions(runtime,'Pi 帮助未声明此适配器所需的工具集控制参数。');
  return {modes:[{id:'read',name:'--tools read,grep,find,ls',description:''},{id:'none',name:'--no-tools',description:''}],
   source:{kind:'adapter',label:'BriefLoop · Pi 工具集控制'},default_mode:'native',auto_available:false,
   note:'Pi 未公开原生权限模式目录；以上为适配器使用 --tools / --no-tools 与 --no-extensions 实现的工具集控制。'};
 }
 if(!ids.length)return unavailablePermissions(runtime,'宿主帮助未返回可识别的权限模式目录；未添加预设模式。');
 const modes=nativeModes(ids,runtime),automatic=modes.some(mode=>mode.id==='auto'&&!mode.disabled);
 // These are explicit BriefLoop launch defaults, not a claim about host config.
 const chosen=automatic?'auto':runtime==='zcode'&&ids.includes('build')?'build':runtime==='codex'&&ids.includes('workspace-write')?'workspace-write':'native';
 return {modes,source:{kind:'runtime',label:runtime+' --help '+flag},default_mode:chosen,auto_available:automatic,
  ...(chosen!=='native'?{default_source:{kind:'adapter',label:'BriefLoop 默认启动选择'}}:{}),
  ...(nativeDefault?{native_default_mode:nativeDefault}:{}),refreshed_at:new Date().toISOString()};
}

export function selectDiscoveredMode(catalog:any,requested:any,runtime:string):string{
 const mode=requested??catalog.default_mode??'native';
 if(mode==='native'){
  // ZCode documents yolo as its headless default. Inherit must not omit --mode
  // and accidentally escalate; preserve the adapter's existing safe build policy.
  if(runtime==='zcode'){
   if(catalog.modes.some((entry:any)=>entry.id==='build'&&!entry.disabled))return 'build';
   throw Error('ZCode 未公开可用的 build 模式；未启用无界面默认的 yolo。');
  }
  return mode;
 }
 const selected=catalog.modes.find((entry:any)=>entry.id===mode);
 if(!selected||selected.disabled)throw Error(selected?.disabled_reason||'宿主当前未公开权限模式：'+String(mode));
 return mode;
}
