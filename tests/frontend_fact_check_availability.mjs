import test from 'node:test';
import assert from 'node:assert/strict';
import {factCheckAvailability} from '../frontend/fact-check-availability.js';

const capability={standard_review:[{id:'codex',label:'Codex CLI'},{id:'briefloop-native',label:'BriefLoop Agent'}],strict_review:[{id:'briefloop-native',label:'BriefLoop Agent'}]};
const base={allowWeb:true,backend:'claude',reviewMode:'standard',capability,backendLabel:id=>id==='claude'?'Claude Code':id};

test('Claude main chain explains why fact-check is unavailable and names a supported reviewer',()=>{
 const result=factCheckAvailability(base);
 assert.equal(result.enabled,false);
 assert.equal(result.target,'review');
 assert.match(result.reason,/Claude Code.*独立审阅.*Codex CLI/);
});

test('an explicit reviewer enables fact-check without changing the main Claude chain',()=>{
 assert.equal(factCheckAvailability({...base,reviewRuntime:{backend:'codex'}}).enabled,true);
 assert.equal(factCheckAvailability({...base,reviewMode:'strict',reviewRuntime:{backend:'codex'}}).enabled,false);
 assert.equal(factCheckAvailability({...base,reviewMode:'strict',reviewRuntime:{backend:'briefloop-native'}}).enabled,true);
 assert.equal(factCheckAvailability({...base,reviewRuntime:{backend:'codex'},pendingReview:true}).enabled,false);
});

test('offline always blocks public fact-check; absent capabilities never invent a rejection',()=>{
 assert.equal(factCheckAvailability({...base,allowWeb:false,reviewRuntime:{backend:'codex'}}).target,'network');
 assert.equal(factCheckAvailability({...base,capability:null}).enabled,true);
});
