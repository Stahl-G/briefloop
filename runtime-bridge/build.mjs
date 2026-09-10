import {build} from 'esbuild';
import {copyFile} from 'node:fs/promises';
await build({entryPoints:['runtime-bridge/main.ts'],bundle:true,platform:'node',target:'node20',format:'esm',outfile:'src/briefloop/static/runtime-bridge.mjs',banner:{js:'// Includes Apache-2.0 Open Design helpers; see runtime-bridge.LICENSE.txt and runtime-bridge.NOTICE.txt.'}});

await copyFile('third_party/open-design/LICENSE','src/briefloop/static/runtime-bridge.LICENSE.txt');
await copyFile('third_party/open-design/NOTICE.md','src/briefloop/static/runtime-bridge.NOTICE.txt');
