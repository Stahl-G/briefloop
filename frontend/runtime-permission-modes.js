// Preserve runtime names, order and defaults; inheritance belongs to the adapter.
export function permissionChoices(catalog){
 const modes=Array.isArray(catalog?.modes)?catalog.modes.filter(mode=>mode?.id):[];
 const inherit=catalog?.inherit_mode;
 return inherit?.id&&!modes.some(mode=>mode.id===inherit.id)?[...modes,inherit]:modes;
}
export function permissionSelection(catalog,selected){
 const modes=Array.isArray(catalog?.modes)?catalog.modes.filter(mode=>mode?.id):[];
 const choices=permissionChoices(catalog);
 if(selected&&!choices.some(mode=>mode.id===selected&&!mode.disabled))return {modes,selected,unavailable:true};
 const chosen=selected||(choices.some(mode=>mode.id===catalog?.default_mode&&!mode.disabled)?catalog.default_mode:'');
 return {modes,selected:chosen};
}
export function createPermissionDirectory({api,getModel=()=>'',onChange=()=>{}}){
 const catalogs=new Map(),cache=new Map(),pending=new Map(),currentKeys=new Map();
 function keyFor(backend,model){return JSON.stringify([backend,model||''])}
 function get(backend,{model=getModel(backend)}={}){return currentKeys.get(backend)===keyFor(backend,model)?catalogs.get(backend):undefined}
 async function load(backend,{refresh=false,model=getModel(backend)}={}){
  const key=keyFor(backend,model);currentKeys.set(backend,key);
  const publish=catalog=>{if(currentKeys.get(backend)===key){catalogs.set(backend,catalog);onChange(backend,catalog)}return catalog};
  if(pending.has(key))return pending.get(key);
  if(!refresh&&cache.has(key)){const cached=cache.get(key);if(catalogs.get(backend)!==cached)publish(cached);return cached}
  catalogs.delete(backend);
  const route='runtime/permissions?backend='+encodeURIComponent(backend)+(model?'&model='+encodeURIComponent(model):'');
  const request=Promise.resolve().then(()=>api(route)).then(catalog=>{cache.set(key,catalog);return publish(catalog)}).catch(error=>{
   const failed={backend,kind:'unavailable',modes:[],source:{kind:'unavailable',label:'未取得运行端权限目录'},diagnostic:error.message};
   cache.set(key,failed);publish(failed);throw error;
  }).finally(()=>pending.delete(key));
  pending.set(key,request);return request;
 }
 return {catalogs,get,load};
}
