'use strict';
// Called only after the updater verifies the ZIP against the official release
// digest. This prepares a Finder handoff; it never replaces or executes the app.
const fs = require('node:fs/promises');
const path = require('node:path');
const {promisify} = require('node:util');
const execFile = promisify(require('node:child_process').execFile);
async function prepare(file, version) {
  if (process.platform !== 'darwin') throw Error('macOS required');
  const parent = await fs.lstat(path.dirname(file));
  if (!parent.isDirectory() || parent.isSymbolicLink()) throw Error('Unsafe update directory');
  const directory = path.join(path.dirname(file), 'prepared');
  await fs.rm(directory, {recursive: true, force: true});
  await fs.mkdir(directory, {mode: 0o700});
  const run = (binary, args) => execFile(binary, args, {timeout: 120000, maxBuffer: 16 * 1024 * 1024});
  try {
    const {stdout} = await run('/usr/bin/unzip', ['-Z1', file]);
    const entries = stdout.trim().split('\n');
    if (!entries.length || entries.some(entry => !entry.startsWith('BriefLoop.app/') ||
        entry.includes('\\') || entry.split('/').some(part => part === '..' || part === '.'))) throw Error('Invalid app archive');
    await run('/usr/bin/ditto', ['-x', '-k', file, directory]);
    const app = path.join(directory, 'BriefLoop.app');
    const stat = await fs.lstat(app);
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw Error('Invalid app bundle');
    const plist = path.join(app, 'Contents/Info.plist');
    for (const [key, expected] of [['CFBundleIdentifier','ai.briefloop.desktop'],['CFBundleShortVersionString',version]]) {
      const {stdout} = await run('/usr/libexec/PlistBuddy', ['-c', `Print ${key}`, plist]);
      if (stdout.trim() !== expected) throw Error('Application identity mismatch');
    }
    await fs.symlink('/Applications', path.join(directory, 'Applications'));
    return directory;
  } catch (error) {await fs.rm(directory, {recursive: true, force: true}); throw error;}
}
module.exports = {prepare};
