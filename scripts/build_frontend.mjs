import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { collectFrontendLicenses, renderFrontendLicenses } from './build_frontend_licenses.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const check = process.argv.includes('--check');
const result = await build({ absWorkingDir: root, entryPoints: ['frontend/app.js'],
  bundle: true, format: 'esm', minify: true, outfile: 'src/briefloop/static/app.js',
  metafile: true, write: false });
const rows = collectFrontendLicenses(result.metafile, root);
const artifacts = [...result.outputFiles.map(file => ({ path: file.path, contents: file.contents })),
  { path: path.join(root, 'src/briefloop/static/frontend-licenses.txt'),
    contents: Buffer.from(renderFrontendLicenses(rows)) }];
let stale = false;
for (const artifact of artifacts) {
  if (check) {
    if (!fs.existsSync(artifact.path) || !fs.readFileSync(artifact.path).equals(Buffer.from(artifact.contents))) {
      console.error(`Outdated generated file: ${path.relative(root, artifact.path)}; run npm run build`);
      stale = true;
    }
  } else {
    fs.mkdirSync(path.dirname(artifact.path), { recursive: true });
    fs.writeFileSync(artifact.path, artifact.contents);
  }
}
if (stale) process.exitCode = 1;
else console.log(`Frontend ${check ? 'verified' : 'built'}; ${rows.length} bundled package license records.`);
