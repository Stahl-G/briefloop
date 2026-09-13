'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const {buildBlockMap} = require('app-builder-lib/out/targets/blockmap/blockmap');
const {readMap} = require('../differential-download.cjs');
async function prepare(file) {
  if (!file.endsWith('.dmg') && !file.endsWith('-mac.zip')) throw new Error('Expected a final Mac DMG or ZIP file');
  const stat = await fs.stat(file);
  await buildBlockMap(file, 'gzip', file + '.blockmap');
  await readMap({body: require('node:fs').createReadStream(file + '.blockmap')}, stat.size);
  return file + '.blockmap';
}
async function main() {
  const directory = path.resolve(process.argv[2] || path.join(__dirname, '../dist'));
  const version = require('../package.json').version;
  for (const suffix of ['arm64.dmg', 'arm64-mac.zip']) {
    console.log(await prepare(path.join(directory, `BriefLoop-${version}-${suffix}`)));
  }
}
if (require.main === module) main().catch(error => {console.error(error.message);process.exitCode = 1;});
module.exports = {prepare};
