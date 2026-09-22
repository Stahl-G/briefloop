// Focused migration guard: authoritative UI components, not document content,
// vendored brand artwork or the retained legacy layout.
import fs from 'node:fs';
import {transform} from 'esbuild';

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
if(failed)process.exitCode=1;
else console.log('Shared UI styles: tokens, typography, radii, stacking and CSS syntax verified. Legacy styles and rendered behavior require separate checks.');
