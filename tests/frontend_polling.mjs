import test from 'node:test';
import assert from 'node:assert/strict';
import {adaptivePoll} from '../frontend/polling.js';

test('polling backs off idle/hidden, refreshes on return and never overlaps a slow request',async()=>{
 const timers=new Map();let sequence=0,listener,active=false,release,calls=0;
 const doc={hidden:false,addEventListener:(_,fn)=>listener=fn,removeEventListener:()=>listener=null};
 const stop=adaptivePoll(()=>{calls++;return new Promise(r=>release=r)},{active:()=>active,fast:1300,doc,
  setTimer:(fn,ms)=>{const id=++sequence;timers.set(id,{fn,ms});return id},clearTimer:id=>timers.delete(id)});
 const next=()=>[...timers.values()][0];
 assert.equal(next().ms,15000);
 const initial=next();timers.clear();const pending=initial.fn();
 assert.equal(calls,1);assert.equal(timers.size,0);
 doc.hidden=true;listener();assert.equal(next().ms,60000);
 doc.hidden=false;listener();assert.equal(calls,1);
 active=true;release();await pending;
 assert.equal(next().ms,0);
 const immediate=next();timers.clear();const second=immediate.fn();release();await second;
 assert.equal(next().ms,1300);
 doc.hidden=true;listener();assert.equal(next().ms,60000);
 stop();assert.equal(timers.size,0);assert.equal(listener,null);
});
