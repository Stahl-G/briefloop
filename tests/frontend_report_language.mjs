// The setup form's report language: legacy values normalize, English presets count
// words, and picking a built-in layout sets the language it was written for.
import assert from 'node:assert/strict';
const nodes={};
const node=id=>nodes[id]||(nodes[id]={id,value:'',textContent:'',options:[],listeners:[],addEventListener(kind,fn){this.listeners.push(fn)}});
globalThis.document={getElementById:node};
const {reportLanguage,lengthUnit,runLanguage,presetLabel,reportLanguageUI}=await import('../frontend/report-language.js');

assert.equal(reportLanguage('中文'),'zh');
assert.equal(reportLanguage('English'),'en');
assert.equal(reportLanguage(undefined),'zh');
assert.equal(reportLanguage('日本語'),null);
assert.equal(lengthUnit('en'),'词');
assert.equal(runLanguage({runs:[{id:'r',requirements:JSON.stringify({language:'English'})}]},'r'),'en');
assert.equal(presetLabel('en','balanced'),'标准 · 1,000 词 / 最多 1,300');
console.log('PASS: legacy language values normalize and English presets count words');

node('length-preset').options=[{value:'balanced',textContent:''}];
const changes=[],notices=[];
const form=reportLanguageUI({notice:text=>notices.push(text),onChange:language=>changes.push(language)});
form.init();
form.restore('English');
assert.equal(node('report-language').value,'en');
assert.equal(node('max-words-label').textContent,'词数上限');
assert.deepEqual(changes,[],'restoring saved requirements must not reset the saved lengths');
for(const template of [undefined,null,{}, {language_hint:null},{language_hint:''},{language_hint:' '},{language_hint:'日本語'}]){
 form.syncTemplate(template);
 assert.equal(node('report-language').value,'en','uploads and cleared or unknown layouts must preserve English');
 assert.deepEqual(changes,[],'a missing language preference must not reset lengths');
 assert.deepEqual(notices,[],'unchanged language must not display a switch notice');
}
form.syncTemplate({language_hint:'zh'});
assert.equal(node('report-language').value,'zh');
assert.deepEqual(changes,['zh']);
assert.match(notices[0],/中文版式/);
form.syncTemplate({language_hint:null});
assert.deepEqual(changes,['zh'],'uploaded layouts carry no language and leave the choice alone');
node('report-language').value='en';node('report-language').listeners.forEach(fn=>fn());
assert.deepEqual(changes,['zh','en']);
assert.equal(node('length-preset').options[0].textContent,'标准 · 1,000 词 / 最多 1,300');
console.log('PASS: a built-in layout sets its language and a manual change re-labels the presets');
