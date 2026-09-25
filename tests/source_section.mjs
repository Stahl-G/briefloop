// Shared slicing for tests that read frontend sources between two anchors.
// A drifted anchor used to make indexOf return -1, and slice(start, -1) then
// matched most of the file instead of the intended section.
import fs from 'node:fs';

export function section(source,startAnchor,endAnchor,file='source'){
 const start=source.indexOf(startAnchor);
 if(start<0)throw Error(`section start anchor not found in ${file}: ${JSON.stringify(startAnchor)}`);
 const end=source.indexOf(endAnchor,start);
 if(end<0)throw Error(`section end anchor not found in ${file}: ${JSON.stringify(endAnchor)}`);
 return source.slice(start,end);
}

// Concatenated text of every frontend/*.js, for "the frontend must not
// contain X" assertions that used to scan app.js alone.
export function allFrontendSources(){
 return fs.readdirSync(new URL('../frontend',import.meta.url))
  .filter(name=>name.endsWith('.js')).sort()
  .map(name=>fs.readFileSync(new URL('../frontend/'+name,import.meta.url),'utf8')).join('\n');
}
