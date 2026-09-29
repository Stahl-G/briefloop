'use strict';

// Records already-built release artifacts; never builds, uploads, or rewrites them.
const fs = require('node:fs/promises');
const {constants} = require('node:fs');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {parseReleaseManifest} = require('../release-metadata.cjs');

const DEFAULT_DIST = path.resolve(__dirname, '../dist');
const PROJECT = path.resolve(__dirname, '../../../pyproject.toml');
const MAX_ASSET = 4 * 1024 ** 3;

async function productVersion() {
  const source = await fs.readFile(PROJECT, 'utf8');
  const project = /^\[project\][ \t]*\r?\n([\s\S]*?)(?=^\[|$(?![\s\S]))/m.exec(source)?.[1];
  const version = /^version[ \t]*=[ \t]*["']([^"'\r\n]+)["'][ \t]*(?:#[^\r\n]*)?\r?$/m.exec(project || '')?.[1];
  if (!version) throw Error('Cannot read [project].version from pyproject.toml.');
  return version;
}

function namesFor(version) {
  return new Set([
    ...['arm64.dmg', 'arm64-mac.zip'].flatMap(suffix => {
      const name = `BriefLoop-${version}-${suffix}`;
      return [name, name + '.blockmap'];
    }),
    `BriefLoop-Setup-${version}-x64.exe`, `BriefLoop-Setup-${version}-x64.exe.blockmap`,
    `briefloop-${version}-py3-none-any.whl`, `briefloop-${version}.tar.gz`,
    'latest-mac.yml', 'latest.yml', 'mac-validation.json', 'windows-validation.json', 'windows-versions.json',
  ]);
}

async function hashAsset(file) {
  const before = await fs.lstat(file);
  if (!before.isFile() || before.isSymbolicLink() || before.size <= 0 || before.size > MAX_ASSET) {
    throw Error(`Invalid release artifact: ${path.basename(file)}`);
  }
  const handle = await fs.open(file, constants.O_RDONLY | (constants.O_NOFOLLOW || 0));
  try {
    const opened = await handle.stat();
    if (opened.dev !== before.dev || opened.ino !== before.ino) throw Error('Release artifact changed before hashing.');
    const hash = createHash('sha256');
    let bytes = 0;
    for await (const chunk of handle.createReadStream({autoClose: false})) {
      bytes += chunk.length;
      if (bytes > MAX_ASSET) throw Error('Release artifact exceeds 4 GiB.');
      hash.update(chunk);
    }
    const after = await handle.stat();
    if (bytes !== before.size || after.size !== before.size || after.mtimeMs !== before.mtimeMs
        || after.ctimeMs !== before.ctimeMs) throw Error('Release artifact changed while hashing.');
    return {bytes, sha256: hash.digest('hex')};
  } finally {await handle.close();}
}

async function prepareReleaseManifest({dist = DEFAULT_DIST, version, commit} = {}) {
  const expectedVersion = await productVersion();
  if (version !== undefined && version !== expectedVersion) throw Error('--version must match pyproject.toml.');
  version = expectedVersion;
  if (typeof commit !== 'string' || !/^[a-f0-9]{40}$/i.test(commit)) throw Error('--commit must be an explicit frozen 40-character commit SHA.');
  const directory = path.resolve(dist), accepted = namesFor(version), assets = {};
  for (const name of (await fs.readdir(directory)).sort()) {
    if (!accepted.has(name)) {
      if (/^(?:BriefLoop-.*\.(?:zip|dmg|exe)(?:\.blockmap)?|briefloop-.*\.(?:whl|tar\.gz))$/.test(name)) {
        throw Error(`Release artifact does not match the product version or supported platform: ${name}`);
      }
      continue;
    }
    assets[name] = await hashAsset(path.join(directory, name));
  }
  if (!Object.keys(assets).some(name => /\.(?:zip|dmg|exe|whl|tar\.gz)$/.test(name))) {
    throw Error('No release package found in --dist.');
  }
  const manifest = {version, source_commit: commit.toLowerCase(), assets};
  parseReleaseManifest(manifest, {tag: `v${version}`});
  const content = JSON.stringify(manifest, null, 2) + '\n';
  const output = path.join(directory, 'release-manifest.json');
  try {
    const existing = await fs.lstat(output);
    if (!existing.isFile() || existing.isSymbolicLink()) throw Error('Existing release manifest is not a regular file.');
    if (await fs.readFile(output, 'utf8') === content) return {file: output, manifest, created: false};
    throw Error('Refusing to replace an existing release-manifest.json with different contents.');
  } catch (error) {if (error.code !== 'ENOENT') throw error;}
  const temporary = await fs.mkdtemp(path.join(directory, '.release-manifest-'));
  try {
    const staged = path.join(temporary, 'release-manifest.json');
    await fs.writeFile(staged, content, {flag: 'wx', mode: 0o644});
    // A hard link installs the complete file atomically and cannot overwrite a
    // manifest created by another release operation in the meantime.
    await fs.link(staged, output);
  } finally {await fs.rm(temporary, {recursive: true, force: true});}
  return {file: output, manifest, created: true};
}

async function main(args = process.argv.slice(2)) {
  const options = {}, allowed = new Set(['--dist', '--version', '--commit']);
  for (let i = 0; i < args.length; i += 2) {
    const option = args[i], value = args[i + 1];
    if (!allowed.has(option) || !value || value.startsWith('--') || Object.hasOwn(options, option.slice(2))) {
      throw Error('Usage: node scripts/prepare-release-manifest.cjs --commit FROZEN_SHA [--dist DIRECTORY] [--version VERSION]');
    }
    options[option.slice(2)] = value;
  }
  const result = await prepareReleaseManifest(options);
  console.log(`${result.created ? 'Generated' : 'Unchanged'} ${result.file}`);
}

if (require.main === module) main().catch(error => {console.error(error.message); process.exitCode = 1;});
module.exports = {prepareReleaseManifest, productVersion};
