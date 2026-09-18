import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const html=fs.readFileSync(new URL('../src/briefloop/static/index.html',import.meta.url),'utf8');
const app=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
const schedules=fs.readFileSync(new URL('../frontend/schedules.js',import.meta.url),'utf8');
const genre=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');

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
  assert.match(html,/id="home-rail"/);
  assert.match(html,/id="home-rail-jobs"/);
  assert.match(html,/id="home-rail-recent"/);
  assert.match(html,/chat-main-col/);
});

test('renderHome shows rail for jobs or reports and keeps recent only in rail',()=>{
  assert.match(app,/has-home-rail/);
  assert.match(app,/home-rail-jobs/);
  assert.match(app,/home-rail-recent-list/);
  assert.match(app,/home-block-recent'\)\)\$\('home-block-recent'\).hidden=true|home-block-recent.*/);
  const renderHome=app.slice(app.indexOf('function renderHome()'),app.indexOf('function autoOpenActivity'));
  assert.match(renderHome,/has-home-rail/);
  assert.match(renderHome,/home-rail-recent-list/);
  assert.match(renderHome,/home-block-recent/);
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

test('GENRE_META avoids primary/danger hue collisions for academic and markets',()=>{
  assert.match(genre,/'学术论文'[^}]*color:'#00695C'/);
  assert.match(genre,/'券商研报'[^}]*color:'#AD1457'/);
  assert.doesNotMatch(genre,/'学术论文'[^}]*color:'#006838'/);
  assert.doesNotMatch(genre,/'券商研报'[^}]*color:'#C62828'/);
});
