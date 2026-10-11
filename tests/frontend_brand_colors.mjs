import test from 'node:test';
import assert from 'node:assert/strict';
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

