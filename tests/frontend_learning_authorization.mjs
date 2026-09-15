import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
function block(name){
 const start=source.indexOf(`function ${name}(`);assert.ok(start>=0,name);
 const prefix=source.lastIndexOf('\n',start)+1,end=source.indexOf('\n}',start);
 const line=source.slice(prefix,source.indexOf('\n',start));
 return line.trimEnd().endsWith('}')&&!line.trimEnd().endsWith('{')?line:source.slice(prefix,end+2);
}
const plan={cases:3,rounds:1,rounds_with_explicit_requirement:2,trial_generations_per_round:6,max_trial_generations:12,backend_label:'Codex CLI',model:'gpt-5.6-luna',web:false,price:'unknown'};

function context(answer){
 const calls=[],prompts=[];
 const ctx=vm.createContext({state:{learning_authorization:{state:'needs_confirmation',authorized_rounds:null,plan}},
  confirm:text=>{prompts.push(text);return answer},api:async(name,body)=>{calls.push([name,body]);return {}}});
 vm.runInContext(['learningPlanText','confirmLearning','setAutoLearn','learningAuthorizationNote'].map(block).join('\n'),ctx);
 return {ctx,calls,prompts};
}

test('turning automatic learning on shows the bound and records only a confirmation',async()=>{
 const cancelled=context(false);
 assert.equal(await cancelled.ctx.setAutoLearn(true),false);
 assert.deepEqual(cancelled.calls,[]);
 assert.match(cancelled.prompts[0],/每轮最多用 3 份历史报告/);
 assert.match(cancelled.prompts[0],/试写合计不超过 12 次/);
 assert.match(cancelled.prompts[0],/Codex CLI · gpt-5\.6-luna/);
 assert.match(cancelled.prompts[0],/无法估算金额/);
 const accepted=context(true);
 assert.equal(await accepted.ctx.setAutoLearn(true),true);
 assert.equal(JSON.stringify(accepted.calls),JSON.stringify([['settings',{auto_learn:true,confirm_learning_rounds:1}]]));
 const off=context(false);
 assert.equal(await off.ctx.setAutoLearn(false),false);
 assert.equal(JSON.stringify(off.calls),JSON.stringify([['settings',{auto_learn:false}]]));
 assert.deepEqual(off.prompts,[],'turning it off needs no confirmation');
});

test('the settings note explains an upgraded workspace and a raised round count',()=>{
 const {ctx}=context(false);
 assert.match(ctx.learningAuthorizationNote(),/自动学习等待确认.*反馈照常保存，已启用的技能照常用于报告/);
 ctx.state.learning_authorization={state:'rounds_exceed',authorized_rounds:2,plan};
 assert.match(ctx.learningAuthorizationNote(),/已高于确认时的 2 轮/);
 ctx.state.learning_authorization={state:'authorized',authorized_rounds:2,plan};
 assert.equal(ctx.learningAuthorizationNote(),'');
});

test('manual learning and the report options use the same confirmation',()=>{
 assert.match(source,/if\(!confirmLearning\('现在用已保存的反馈启动一次学习验证。'\)\)return;const result=await api\('learn',\{confirmed:true\}\)/);
 assert.match(source,/if\(key==='learn'\)\{try\{await setAutoLearn\(value\)/);
 assert.match(source,/data-option="learn">自动学习<\/label>/);
 assert.doesNotMatch(source,/data-option="learn" checked/);
 assert.match(source,/learn:state\.learning_authorization\?\.state==='authorized'/);
});
