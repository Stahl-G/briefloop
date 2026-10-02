'use strict';
// Release acceptance under the packaged Electron's Node mode, with synthetic data.
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');

async function main() {
  const [resourceArg, dataArg, outputArg] = process.argv.slice(2);
  if (!process.versions.electron || !resourceArg || !dataArg || !outputArg) {
    throw Error('Use packaged Electron in Node mode: SCRIPT RESOURCES ISOLATED_DATA OUTPUT_JSON');
  }
  const resources = path.resolve(resourceArg), data = path.resolve(dataArg), output = path.resolve(outputArg);
  const archive = path.join(resources, 'app.asar');
  const {createEnvironment, runOwnedProcess} = require(path.join(archive, 'environment.cjs'));
  const {WorkspaceService, runtimeLaunch} = require(path.join(archive, 'service.cjs'));
  const pkg = require(path.join(archive, 'package.json'));
  const manifest = JSON.parse(await fs.readFile(path.join(resources, 'backend/manifest.json'), 'utf8'));
  assert.equal(pkg.version, manifest.version);
  const environment = createEnvironment({app: {getPath: () => data, getVersion: () => pkg.version},
    payloadPath: path.join(resources, 'backend')});
  const prepared = await environment.prepare();
  assert.equal(prepared.state, 'ready', JSON.stringify(prepared.error));
  const runtime = environment.runtime(), launch = runtimeLaunch(runtime);
  const officeScript = path.resolve(__dirname, '../../../tests/check_office_exports.py');
  const office = await runOwnedProcess(launch.executable,
    [...launch.args, officeScript, path.join(data, 'office-evidence')], {env: launch.env, timeoutMs: 120000});
  const exported = JSON.parse(office.stdout.trim());
  assert.equal(exported.version, pkg.version);
  assert.equal(exported.pi, '1.0.0');
  let service;
  try {
    service = new WorkspaceService(runtime);
    await service.start(path.join(data, 'synthetic-workspace'), {create: true});
    const actual = await service.request('/api/software-version');
    assert.equal(actual.version, pkg.version);
    assert.equal(actual.release_notes.version, pkg.version);
    assert.equal(actual.release_notes.state, 'loaded');
    assert.equal((await service.status()).busy, false);
  } finally {if (service) await service.stop();}
  const exited = await service.exited;
  assert.equal(exited.code, 0, JSON.stringify(exited));
  assert.equal(exited.signal, null);
  await fs.writeFile(output, JSON.stringify({version: pkg.version, source_commit: manifest.source_commit,
    release_wheel_sha256: manifest.sha256, platform: process.platform, electron: process.versions.electron,
    packaged_asar_modules: 'passed', environment_preparation: 'ready', owned_service_startup: 'passed',
    owned_service_exit: 'passed', version_notes: 'passed', office_exports: exported,
    native_window: 'unverified by this headless check', office_gui: 'unverified', model_calls: 0}, null, 2) + '\n');
  console.log('Packaged backend, Pi and Office exports verified:', output);
}
main().catch(error => {console.error(error); process.exitCode = 1;});
