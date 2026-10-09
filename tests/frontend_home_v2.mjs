import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {homeUI} from '../frontend/home.js';
import {section,allFrontendSources} from './source_section.mjs';

const html=fs.readFileSync(new URL('../src/briefloop/static/index.html',import.meta.url),'utf8');
const app=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
const home=fs.readFileSync(new URL('../frontend/home.js',import.meta.url),'utf8');
const schedules=fs.readFileSync(new URL('../frontend/schedules.js',import.meta.url),'utf8');
const genre=fs.readFileSync(new URL('../frontend/templates.js',import.meta.url),'utf8');
const tokens=fs.readFileSync(new URL('../src/briefloop/static/tokens.css',import.meta.url),'utf8');
const style=fs.readFileSync(new URL('../src/briefloop/static/style.css',import.meta.url),'utf8');
const frontendAll=allFrontendSources();

test('the home task button opens the pending run and respects unsaved edits on repeat clicks',()=>{
 const button={dataset:{railOpenJob:'job'}},nodes=new Map();
 const $=id=>{if(!nodes.has(id))nodes.set(id,{hidden:false,dataset:{},classList:{toggle(){}},
  querySelectorAll:selector=>selector==='[data-rail-open-job]'?[button]:[]});return nodes.get(id)};
 const pages=[],state={jobs:[{id:'job',kind:'generate',status:'running',payload:'{"run_id":"run"}'}],briefs:[],runs:[{id:'run',requirements:'{"title":"正在生成的报告"}'}]};
 const context=vm.createContext({$,state,current:{id:'old'},pendingRun:null,dirty:false,saving:false,editor:null,
  openBrief:()=>false,parse:value=>JSON.parse(value||'{}'),notice(){},page:name=>pages.push(name),refreshProgress(){}});
 vm.runInContext(section(app,'function showPendingReport(','function tryOpenPending(','frontend/app.js'),context);
 vm.runInContext(section(app,'function taskFor(','const taskSnapshots=','frontend/app.js'),context);
 const home=homeUI({$,getState:()=>state,parse:context.parse,taskLabel:()=> '生成报告',taskFor:context.taskFor,openTask:context.openTask});
 home.renderHomeTasks();button.onclick();button.onclick();
 assert.equal(context.pendingRun,'run');assert.equal(context.current,null);assert.deepEqual(pages,['report','report']);
 assert.equal($('report-title').textContent,'正在生成的报告');assert.equal($('export-menu-toggle').hidden,true);
 context.current={id:'unsaved'};context.dirty=true;button.onclick();
 assert.equal(context.current.id,'unsaved');assert.equal(pages.length,2,'task navigation cannot discard unsaved edits');
});

test('home loads tokens.css and keeps composer progressive disclosure markers',()=>{
  assert.match(html,/href="\/tokens\.css"/);
  assert.match(html,/id="composer-params"/);
  assert.match(html,/id="composer-params-panel"/);
  assert.match(html,/id="new-report"[^>]*class="[^"]*primary/);
  assert.match(html,/id="home-block-schedule"[^>]*hidden/);
  assert.match(html,/id="home-block-recent"[^>]*hidden/);
  for(const id of ['home-start-report','home-import-previous','home-report-form'])
    assert.match(html,new RegExp('id="'+id+'"'));
  assert.match(html,/id="home-rail"/);
  assert.match(html,/id="home-rail-jobs"/);
  assert.match(html,/id="home-rail-recent"/);
  assert.match(html,/chat-main-col/);
});

test('renderHome shows main recent when rows exist and rail for running jobs',()=>{
  assert.match(home,/has-home-rail/);
  assert.match(home,/home-rail-jobs/);
  assert.match(home,/home-rail-recent-list/);
  const renderHome=section(home,'function renderHome()','function homeReportRowHTML(','frontend/home.js');
  const rail=section(home,'function renderHomeTasks()','function homeReportRowHTML(','frontend/home.js');
  assert.match(rail,/has-home-rail/);
  assert.match(rail,/home-rail-jobs/);
  assert.match(renderHome,/home-block-recent/);
  assert.match(renderHome,/mainBox.innerHTML=rows.map\(homeReportRowHTML\)/);
});

test('home does not render empty schedule/report placeholders',()=>{
  assert.doesNotMatch(schedules,/还没有计划/);
  assert.match(schedules,/home-block-schedule/);
  const renderHome=section(home,'function renderHome()',' return {bannerTitle','frontend/home.js');
  assert.doesNotMatch(renderHome,/还没有报告/);
  assert.match(renderHome,/home-block-recent/);
  assert.match(app,/anchoredPopover\(\{trigger:\$\('composer-params'\)/);
  assert.match(app,/composer-params-panel/);
});

test('the category palette lives in the tokens and avoids the status hues',()=>{
  // The palette used to be written three times: here as hex in GENRE_META, as
  // per-element CSS rules with the hex repeated as a fallback, and in the
  // tokens. Only the tokens carry a value now.
  assert.match(tokens,/--c-cat-violet-fg:\s*#6741D9/);
  assert.match(tokens,/--c-cat-magenta-fg:\s*#A3195B/);
  assert.doesNotMatch(tokens,/--c-cat-[a-z]+-fg:\s*#2448B8/);   // the primary blue
  assert.doesNotMatch(tokens,/--c-cat-[a-z]+-fg:\s*#C62828/);   // the danger red
  assert.match(genre,/'学术论文'[^}]*cat:'cat-academic'/);
  assert.match(genre,/'券商研报'[^}]*cat:'cat-markets'/);
  assert.doesNotMatch(frontendAll,/tile:'#|color:'#/);
  for(const cat of ['business','markets','academic','collab','neutral'])
    assert.match(style,new RegExp(`\\.cat-${cat}\\{background:var\\(--cat-${cat}-bg\\);color:var\\(--cat-${cat}-fg\\)\\}`));
});

 test('home hydrates linear ICONS from settings icon set',()=>{
  assert.match(home,/svgLineIcon/);
  assert.match(home,/hydrateHomeIcons/);
  assert.match(home,/reportIconMeta/);
  assert.match(home,/ICONS\[name\]/);
});

test('the Opencode effort field is not wired to the model picker',()=>{
  // setupModelPickers() converts every input[list="model-suggestions"] into a
  // model selector: it strips the list, wraps the input and hangs a dropdown of
  // every model off it. A reasoning-level field must never carry that list, or
  // picking from the dropdown writes a model ID where an effort belongs.
  const field=html.match(/<input id="chat-variant"[^>]*>/)[0];
  assert.doesNotMatch(field,/list="model-suggestions"/);
  assert.match(field,/list="effort-suggestions"/);
  assert.match(html,/<datalist id="effort-suggestions">(?:<option value="(?:low|medium|high|max)"><\/option>)+<\/datalist>/);
  // one name for one concept, in the composer and in both settings surfaces
  assert.match(html,/<label class="params-field" for="chat-variant"[^>]*><span>推理强度<\/span>/);
  assert.match(html,/<label id="variant-field" hidden>推理强度<input id="model-variant" list="effort-suggestions"/);
  assert.match(app,/role-variant-field[^`]*<span>推理强度<\/span>/);
  for(const id of ['model-variant','role-\\$\\{role\\}-variant'])
    assert.doesNotMatch(html+frontendAll,new RegExp('id="'+id+'"[^>]*list="model-suggestions"'));
});
