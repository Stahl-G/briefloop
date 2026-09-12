import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { build } from 'esbuild';
import { collectFrontendLicenses, renderFrontendLicenses } from '../scripts/build_frontend_licenses.mjs';

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'briefloop-licenses-'));
const write = (name, text) => { const target = path.join(root, name);
  fs.mkdirSync(path.dirname(target), { recursive: true }); fs.writeFileSync(target, text); };
function pkg(location, name, version, license, source, notice) {
  write(`${location}/package.json`, JSON.stringify({ name, version, license, main: 'index.js', sideEffects: false }));
  write(`${location}/index.js`, source);
  if (notice) write(`${location}/LICENSE.txt`, notice);
}
try {
  pkg('node_modules/outer', 'outer', '1.0.0', 'MIT', "import {value as inner} from 'shared'; export const value = inner + 10;", 'Outer license');
  pkg('node_modules/outer/node_modules/shared', 'shared', '1.0.0', 'MIT', 'export const value = 1;', 'Nested license');
  pkg('node_modules/shared', 'shared', '2.0.0', 'Apache-2.0', 'export const value = 2;', 'Apache license');
  write('node_modules/shared/NOTICE', 'Retain this copyright notice\n');
  // This installed/imported package has no license, but no code reaches the output.
  pkg('node_modules/unused', 'unused', '1.0.0', 'ISC', 'export const unused = 3;');
  write('entry.js', "import {value as a} from 'outer'; import {value as b} from 'shared'; import {unused} from 'unused'; console.log(a,b);");
  const result = await build({ absWorkingDir: root, entryPoints: ['entry.js'], bundle: true,
    format: 'esm', outfile: 'out.js', metafile: true, write: false });
  const rows = collectFrontendLicenses(result.metafile, root);
  assert.deepEqual(rows.map(row => [row.name, row.version]), [['outer', '1.0.0'], ['shared', '1.0.0'], ['shared', '2.0.0']]);
  const rendered = renderFrontendLicenses(rows);
  assert.match(rendered, /shared 2.0.0 \(Apache-2.0\)/);
  assert.match(rendered, /Nested license/);
  assert.match(rendered, /Retain this copyright notice/);
  const reordered = structuredClone(result.metafile);
  for (const output of Object.values(reordered.outputs)) output.inputs = Object.fromEntries(Object.entries(output.inputs).reverse());
  assert.equal(renderFrontendLicenses(collectFrontendLicenses(reordered, root)), rendered);
  fs.unlinkSync(path.join(root, 'node_modules/outer/node_modules/shared/LICENSE.txt'));
  assert.throws(() => collectFrontendLicenses(result.metafile, root), /Missing license text for shared@1.0.0/);
  write('node_modules/outer/node_modules/shared/LICENSE.txt', 'Nested license');
  fs.unlinkSync(path.join(root, 'node_modules/shared/package.json'));
  assert.throws(() => collectFrontendLicenses(result.metafile, root), /ENOENT/);
  console.log('PASS: actual bundle licenses include nested versions and NOTICE, omit unused code, remain deterministic, and fail on missing license or manifest.');
} finally { fs.rmSync(root, { recursive: true, force: true }); }
