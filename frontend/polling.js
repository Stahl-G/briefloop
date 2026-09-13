// Poll serially and reduce wakeups while the UI is idle or hidden.
export function adaptivePoll(task,{active,fast,idle=15000,hidden=60000,doc=document,setTimer=setTimeout,clearTimer=clearTimeout}){
 let timer=null,running=false,stopped=false,wakeAfter=false;
 const delay=()=>doc.hidden?hidden:active()?fast:idle;
 const schedule=ms=>{clearTimer(timer);if(!stopped)timer=setTimer(run,ms)};
 async function run(){
  if(stopped)return;
  if(running){wakeAfter=true;return}
  running=true;
  try{await task()}catch{/* The task owns its user-visible connection state. */}
  finally{running=false;const soon=wakeAfter&&!doc.hidden;wakeAfter=false;schedule(soon?0:delay())}
 }
 function visibility(){if(doc.hidden){wakeAfter=false;schedule(hidden)}else if(running)wakeAfter=true;else schedule(0)}
 doc.addEventListener('visibilitychange',visibility);
 schedule(delay());
 return ()=>{stopped=true;clearTimer(timer);doc.removeEventListener('visibilitychange',visibility)};
}
