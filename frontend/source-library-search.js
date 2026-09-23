// One bounded request after typing stops; more work requires an explicit click.
export function createSourceLibrarySearch({api,onChange,delay=250}){
 let generation=0,timer=null,key='',input=null;
 let state={query:'',items:[],unsearched:[],unsearchedCount:0,scanned:0,total:0,loading:false,exhausted:false,next:null,error:''};
 const notify=()=>onChange(state);
 async function load(cursor,version){
  if(version!==generation)return;
  state={...state,loading:true,error:''};notify();
  const params=new URLSearchParams({q:input.query,type:input.type||'',channel:input.channel||'',status:input.status||''});
  if(cursor)params.set('cursor',cursor);
  try{
   const result=await api('source-search?'+params);
   if(version!==generation)return;
   state={...state,items:[...state.items,...result.items],unsearched:[...state.unsearched,...result.unsearched],
    unsearchedCount:state.unsearchedCount+result.unsearched_count,scanned:state.scanned+result.scanned_candidates,
    total:result.total_candidates,next:result.next_cursor,exhausted:result.exhausted,loading:false};
  }catch(error){if(version!==generation)return;state={...state,loading:false,error:error.message}}
  notify();
 }
 return {
  get state(){return state},
  update(next){
   const nextKey=JSON.stringify(next);if(key===nextKey)return;
   key=nextKey;input=next;const version=++generation;clearTimeout(timer);
   state={query:next.query,items:[],unsearched:[],unsearchedCount:0,scanned:0,total:0,loading:!!next.query,exhausted:false,next:null,error:''};
   if(next.query)timer=setTimeout(()=>load(null,version),delay);
  },
  more(){if(state.next&&!state.loading&&!state.error)load(state.next,generation)},
 };
}
