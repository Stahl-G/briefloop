import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {containsRetiredBrandColor} from '../scripts/brand_color_guard.mjs';

test('retired brand guard catches lowercase, uppercase and Word accent values',()=>{
 for(const color of ['00'+'6838','14'+'5238','e6'+'f2ec']){
  assert.equal(containsRetiredBrandColor(`color:#${color.toLowerCase()}`),true);
  assert.equal(containsRetiredBrandColor(`fill="#${color.toUpperCase()}"`),true);
  assert.equal(containsRetiredBrandColor(`w:val="${color.toUpperCase()}"`),true);
 }
 assert.equal(containsRetiredBrandColor('var(--brand);#2448B8;#1B3A8F;#EEF2FC'),false);
 assert.equal(containsRetiredBrandColor('sha256:ab'+'006'+'838'+'cd0123'),false);
});

test('one-version green aliases resolve only to semantic blue brand aliases',()=>{
 const css=fs.readFileSync(new URL('../frontend/ui-system.css',import.meta.url),'utf8');
 assert.match(css,/--brand:var\(--color-primary\)/);
 assert.match(css,/--brand-hover:var\(--color-primary-hover\)/);
 assert.match(css,/--green:var\(--brand\)/);
 assert.match(css,/--green-hover:var\(--brand-hover\)/);
 assert.doesNotMatch(css,/var\(--green(?:-hover)?\)/);
});

test('retained layout does not override v3 radius/shadow tokens or append alpha to var()',()=>{
 const css=fs.readFileSync(new URL('../src/briefloop/static/style.css',import.meta.url),'utf8').split('/* BEGIN GENERATED UI SYSTEM */')[0];
 assert.doesNotMatch(css,/--radius(?:-sm|-pill)?:\s*\d/);
 assert.doesNotMatch(css,/--shadow-sm:/);
 assert.doesNotMatch(css,/var\(--[\w-]+\)[0-9a-f]{2,8}(?=[;}\s])/i);
 assert.match(css,/selectedCell[^}]*var\(--color-primary-soft\)/);
});
