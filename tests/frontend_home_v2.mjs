import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const html=fs.readFileSync(new URL('../src/briefloop/static/index.html',import.meta.url),'utf8');
const app=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
const schedules=fs.readFileSync(new URL('../frontend/schedules.js',import.meta.url),'utf8');
const genre=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
const tokens=fs.readFileSync(new URL('../src/briefloop/static/tokens.css',import.meta.url),'utf8');
const style=fs.readFileSync(new URL('../src/briefloop/static/style.css',import.meta.url),'utf8');

test('home loads tokens.css and keeps composer progressive disclosure markers',()=>{
  assert.match(html,/href="\/tokens\.css"/);
  assert.match(html,/id="composer-params"/);
  assert.match(html,/id="composer-params-panel"/);
  assert.match(html,/id="new-report"[^>]*class="[^"]*primary/);
  assert.match(html,/id="home-block-schedule"[^>]*hidden/);
  assert.match(html,/id="home-block-recent"[^>]*hidden/);
  assert.match(html,/cat-business/);
  assert.match(html,/cat-markets/);
  assert.match(html,/cat-academic/);
  assert.match(html,/data-home-icon="briefcase"/);
  assert.match(html,/data-home-icon="buildingsSlash"/);
  assert.match(html,/cat-collab/);
  assert.match(html,/data-home-icon="chart"/);
  assert.match(html,/data-home-icon="buildingsSlash"/);
  assert.match(html,/cat-collab/);
  assert.match(html,/id="home-rail"/);
  assert.match(html,/id="home-rail-jobs"/);
  assert.match(html,/id="home-rail-recent"/);
  assert.match(html,/chat-main-col/);
});

test('renderHome shows main recent when rows exist and rail for running jobs',()=>{
  assert.match(app,/has-home-rail/);
  assert.match(app,/home-rail-jobs/);
  assert.match(app,/home-rail-recent-list/);
  const renderHome=app.slice(app.indexOf('function renderHome()'),app.indexOf('function homeReportRowHTML()')) || app.slice(app.indexOf('function renderHome()'),app.indexOf('let autoOpenedActivityTurn'));
  const rail=app.slice(app.indexOf('function renderHomeTasks()'),app.indexOf('function homeReportRowHTML('));
  assert.match(rail,/has-home-rail/);
  assert.match(rail,/home-rail-jobs/);
  assert.match(renderHome,/home-block-recent/);
  assert.match(renderHome,/mainBox.innerHTML=rows.map\(homeReportRowHTML\)/);
});

test('home does not render empty schedule/report placeholders',()=>{
  assert.doesNotMatch(schedules,/还没有计划/);
  assert.match(schedules,/home-block-schedule/);
  const renderHome=app.slice(app.indexOf('function renderHome()'),app.indexOf('function autoOpenActivity'));
  assert.doesNotMatch(renderHome,/还没有报告/);
  assert.match(renderHome,/home-block-recent/);
  assert.match(app,/wireComposerParams/);
  assert.match(app,/composer-params-panel/);
});

test('the category palette lives in the tokens and avoids the status hues',()=>{
  // The palette used to be written three times: here as hex in GENRE_META, as
  // per-element CSS rules with the hex repeated as a fallback, and in the
  // tokens. Only the tokens carry a value now.
  assert.match(tokens,/--c-cat-teal-fg:\s*#00695C/);
  assert.match(tokens,/--c-cat-magenta-fg:\s*#AD1457/);
  assert.doesNotMatch(tokens,/--c-cat-[a-z]+-fg:\s*#006838/);   // the primary green
  assert.doesNotMatch(tokens,/--c-cat-[a-z]+-fg:\s*#C62828/);   // the danger red
  assert.match(genre,/'学术论文'[^}]*cat:'cat-academic'/);
  assert.match(genre,/'券商研报'[^}]*cat:'cat-markets'/);
  assert.doesNotMatch(genre,/tile:'#|color:'#/);
  for(const cat of ['business','markets','academic','collab','neutral'])
    assert.match(style,new RegExp(`\\.cat-${cat}\\{background:var\\(--cat-${cat}-bg\\);color:var\\(--cat-${cat}-fg\\)\\}`));
});

 test('home hydrates linear ICONS from settings icon set',()=>{
  assert.match(app,/svgLineIcon/);
  assert.match(app,/hydrateHomeIcons/);
  assert.match(app,/homeReportIconMeta/);
  assert.match(app,/ICONS\[name\]/);
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
  assert.match(html,/<label class="params-field" for="chat-variant"[^>]*><span>推理 effort<\/span>/);
  assert.match(html,/<label id="variant-field" hidden>推理 effort<input id="model-variant" list="effort-suggestions"/);
  assert.match(app,/role-variant-field[^`]*<span>推理 effort<\/span>/);
  for(const id of ['model-variant','role-\\$\\{role\\}-variant'])
    assert.doesNotMatch(html+app,new RegExp('id="'+id+'"[^>]*list="model-suggestions"'));
});
