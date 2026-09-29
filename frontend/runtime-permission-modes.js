// Host directories own permission names and defaults. The UI never equates
// an unknown host mode, or a permissive mode, with Auto by guessing its name.
export function permissionSelection(catalog,selected){
 const modes=Array.isArray(catalog?.modes)?catalog.modes.filter(mode=>mode.id):[];
 if(selected&&!modes.some(mode=>mode.id===selected))return {modes,selected,unavailable:true};
 const chosen=modes.some(mode=>mode.id===selected)?selected:modes.some(mode=>mode.id===catalog?.default_mode)?catalog.default_mode:modes[0]?.id||'';
 return {modes,selected:chosen};
}
export function createPermissionDirectory({api,onChange=()=>{}}){
 const catalogs=new Map(),pending=new Map();
 async function load(backend,{refresh=false}={}){
  if(pending.has(backend))return pending.get(backend);
  if(!refresh&&catalogs.has(backend))return catalogs.get(backend);
  const request=Promise.resolve().then(()=>api('runtime/permissions?backend='+encodeURIComponent(backend))).then(catalog=>{catalogs.set(backend,catalog);onChange(backend,catalog);return catalog}).catch(error=>{catalogs.set(backend,{backend,kind:'unavailable',modes:[],diagnostic:error.message});throw error}).finally(()=>pending.delete(backend));
  pending.set(backend,request);return request;
 }
 return {catalogs,load};
}
