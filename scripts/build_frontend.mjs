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
// Retain the legacy layout during migration; the shared component layer is
// authored once and appended deterministically, never hand-edited in the bundle.
const stylePath = path.join(root, 'src/briefloop/static/style.css');
const systemMarker = '/* BEGIN GENERATED UI SYSTEM */';
const legacyStyle = fs.readFileSync(stylePath, 'utf8').split(systemMarker)[0].trimEnd();
artifacts.push({path: stylePath, contents: Buffer.from(`${legacyStyle}\n\n${systemMarker}\n${fs.readFileSync(path.join(root, 'frontend/ui-system.css'), 'utf8')}`)});
// Desktop starts before the web service. Package the same tokens and mark,
// with byte-for-byte checks so its launcher cannot drift from the app design.
for (const [source, target] of [['tokens.css', 'ui-tokens.css'], ['runtime-briefloop.svg', 'briefloop-mark.svg']]) {
  artifacts.push({path: path.join(root, 'desktop/electron/assets', target),
    contents: fs.readFileSync(path.join(root, 'src/briefloop/static', source))});
}
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
