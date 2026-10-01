// Shared components enforce full token discipline. A separate repository-wide
// guard rejects retired brand colors in legacy layouts, docs and export defaults.
import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {transform} from 'esbuild';
import {containsRetiredBrandColor} from './brand_color_guard.mjs';

const system=fs.readFileSync(new URL('../frontend/ui-system.css',import.meta.url),'utf8');
const tokens=fs.readFileSync(new URL('../src/briefloop/static/tokens.css',import.meta.url),'utf8');
const launcher=fs.readFileSync(new URL('../desktop/electron/welcome.css',import.meta.url),'utf8');
const declared=new Set([...tokens.matchAll(/(--[\w-]+)\s*:/g)].map(m=>m[1]));
const external=new Set(['--swatch-color']); // The selected Word template color.
let failed=false;
for(const [name,source] of [['ui-system.css',system],['welcome.css',launcher]]){
 const css=source.replace(/\/\*[\s\S]*?\*\//g,'');
 const local=new Set([...css.matchAll(/(--[\w-]+)\s*:/g)].map(m=>m[1]));
 const problems=[];
 if(/#[0-9a-f]{3,8}\b|\brgba?\s*\(/i.test(css))problems.push('literal colors outside tokens');
 if(/z-index\s*:\s*-?\d/.test(css))problems.push('literal stacking level');
 if(/(?:font-size|font-weight|line-height|border-radius)\s*:\s*\d/.test(css))problems.push('literal type or radius scale');
 for(const [,token] of css.matchAll(/var\(\s*(--[\w-]+)/g)){
  if(!declared.has(token)&&!local.has(token)&&!external.has(token))problems.push(`undefined token ${token}`);
 }
 const parsed=await transform(source,{loader:'css'});
 for(const warning of parsed.warnings)problems.push(warning.text);
 if(problems.length){failed=true;console.error(`${name}: ${[...new Set(problems)].join('; ')}`);}
}
// Exact retired brand values are forbidden repository-wide, including retained
// layout, documentation, fixtures and export defaults. Split construction keeps
// the checker itself from introducing a forbidden literal.
const root=fileURLToPath(new URL('../',import.meta.url));
for(const name of execFileSync('git',['ls-files','--cached','--others','--exclude-standard','-z'],{cwd:root,encoding:'utf8'}).split('\0').filter(Boolean)){
 const target=path.join(root,name);
 if(!fs.existsSync(target)||!fs.statSync(target).isFile())continue;
 const bytes=fs.readFileSync(target);
 if(bytes.includes(0))continue; // Container formats are checked in asset tests.
 if(/\.css$/.test(name)&&/var\(--[\w-]+\)[0-9a-f]{2,8}(?=[;}\s])/i.test(bytes.toString('utf8'))){failed=true;console.error(`${name}: invalid alpha suffix after a CSS variable`);}
 if(containsRetiredBrandColor(bytes.toString('utf8'))){failed=true;console.error(`${name}: retired brand color; use current semantic tokens/defaults`);}
}
// Finder uses points for icon coordinates; a 96-DPI background is shrunk to
// 75% and overlaps the installer instructions despite correct pixel dimensions.
const dmg=fs.readFileSync(path.join(root,'desktop/electron/assets/dmg-background.png'));
let density;
for(let offset=8;offset+12<=dmg.length;){
 const length=dmg.readUInt32BE(offset),type=dmg.toString('ascii',offset+4,offset+8);
 if(type==='pHYs'&&length===9&&offset+length+12<=dmg.length)density={x:dmg.readUInt32BE(offset+8),y:dmg.readUInt32BE(offset+12),unit:dmg[offset+16]};
 offset+=length+12;
}
if(!density||density.unit!==1||Math.abs(density.x-2835)>1||Math.abs(density.y-2835)>1){
 failed=true;console.error('dmg-background.png: require 72-DPI density for Finder point coordinates');
}
if(failed)process.exitCode=1;
else console.log('Shared UI styles: tokens, typography, radii, stacking and CSS syntax verified. Legacy styles and rendered behavior require separate checks.');
