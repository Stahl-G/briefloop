'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {spawnSync} = require('node:child_process');
const path = require('node:path');
const {reuseWindowsDependencies, COPY_DEPENDENCIES} = require('../windows-dependency-reuse.cjs');

function configuration() {
  return {
    source: path.resolve('old-environment'), target: path.resolve('new-environment'),
    previous: {schema: 1, platform: 'win32', arch: 'x64', requirementsSha256: 'a'.repeat(64),
      hostPython: 'C:\\Python312\\python.exe', pythonVersion: '3.12.5'},
    python: {executable: 'C:\\Python312\\python.exe', version: '3.12.5'},
    manifest: {requirements_sha256: 'a'.repeat(64), requirementsPath: path.resolve('requirements.txt')},
    platform: 'win32', arch: 'x64', signal: new AbortController().signal,
  };
}

test('reuse rejects changed host, version, lock, platform or architecture before running anything', async () => {
  for (const change of [
    {platform: 'darwin'}, {arch: 'arm64'},
    {python: {executable: 'C:\\Python313\\python.exe', version: '3.12.5'}},
    {python: {executable: 'C:\\Python312\\python.exe', version: '3.13.0'}},
    {manifest: {requirements_sha256: 'b'.repeat(64)}},
    {previous: {}},
  ]) {
    const result = await reuseWindowsDependencies({...configuration(), ...change,
      run: async () => assert.fail('Ineligible source must not be executed or copied')});
    assert.equal(result.eligible, false);
  }
});

test('eligible reuse invokes only the fresh interpreter with site initialization disabled', async () => {
  const config = configuration(); let called = false;
  const result = await reuseWindowsDependencies({...config, run: async (executable, args, signal, timeout) => {
    called = true;
    assert.equal(executable, path.join(config.target, 'Scripts', 'python.exe'));
    assert.deepEqual(args.slice(0, 3), ['-I', '-S', '-c']);
    assert.equal(args[3], COPY_DEPENDENCIES);
    assert.deepEqual(args.slice(4, 7), [config.source, config.target, config.manifest.requirementsPath]);
    assert.equal(JSON.parse(args[7]).platform, 'win32');
    assert.equal(signal, config.signal); assert.equal(timeout, 120000);
    return {stdout: JSON.stringify({reused: ['lxml'], skipped: ['pywin32']})};
  }});
  assert.equal(called, true);
  assert.deepEqual(result, {eligible: true, reused: ['lxml'], skipped: ['pywin32']});
  const failure = Object.assign(Error('unconfirmed cleanup'), {code: 'cleanup_failed'});
  await assert.rejects(reuseWindowsDependencies({...config, run: async () => {throw failure}}), error => error === failure);
});

// These are real filesystem/hash/metadata operations on synthetic wheel layouts.
// They intentionally do not execute a copied extension or claim native Windows
// launcher, DLL loading, or service-start validation on a non-Windows runner.
test('synthetic installed-wheel audit copies safe files and rejects unsupported or altered distributions', t => {
  const python = process.platform === 'win32' ? 'python' : 'python3';
  const available = spawnSync(python, ['-I', '-c', 'import pip._vendor.packaging'], {encoding: 'utf8'});
  if (available.error || available.status !== 0) {t.skip('Existing Python with pip is required for the read-only fixture runner'); return;}
  const script = String.raw`
import base64, csv, hashlib, io, json, os, pathlib, sys, tempfile
scope = {'__name__': 'reuse_fixture'}
exec(${JSON.stringify(COPY_DEPENDENCIES)}, scope)
copy_dependencies = scope['copy_dependencies']

with tempfile.TemporaryDirectory(prefix='briefloop-reuse-fixture-') as directory:
    root = pathlib.Path(directory).resolve()
    source, target = root/'source', root/'target'
    old_site, new_site = source/'Lib'/'site-packages', target/'Lib'/'site-packages'
    old_site.mkdir(parents=True); new_site.mkdir(parents=True)
    (source/'Scripts').mkdir(); (source/'Scripts'/'old.exe').write_bytes(b'old launcher')
    (source/'pyvenv.cfg').write_text('old environment')
    (new_site/'bootstrap-marker').write_bytes(b'fresh bootstrap')
    names = []

    def distribution(name, files=None, extra_record=None, entry=False, tag='py3-none-any'):
        names.append(name)
        info = name.replace('-', '_') + '-1.0.dist-info'
        values = {name.replace('-', '_')+'/__init__.py': b'VALUE = 1\n',
                  info+'/METADATA': ('Metadata-Version: 2.1\nName: '+name+'\nVersion: 1.0\n').encode(),
                  info+'/WHEEL': ('Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: '+tag+'\n').encode()}
        values.update(files or {})
        if entry:
            values[info+'/entry_points.txt'] = b'[console_scripts]\nexample = example:main\n'
        rows = []
        for relative, data in values.items():
            location = old_site/relative
            location.parent.mkdir(parents=True, exist_ok=True); location.write_bytes(data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=')
            rows.append([relative, 'sha256='+digest, str(len(data))])
        rows.extend(extra_record or [])
        rows.append([info+'/RECORD', '', ''])
        with (old_site/info/'RECORD').open('w', newline='') as stream: csv.writer(stream).writerows(rows)
        return info

    distribution('safe', {'safe/native.pyd': b'synthetic extension bytes', 'safe.libs/runtime.dll': b'synthetic DLL bytes'})
    distribution('relative-path', {'relative_path.pth': b'# local path only\nrelative_path\n'})
    distribution('tampered'); (old_site/'tampered'/'__init__.py').write_bytes(b'changed')
    info = distribution('metadata-changed'); (old_site/info/'WHEEL').write_text('Wheel-Version: 1.0\nTag: py3-none-any\n# changed\n')
    distribution('executable-path', {'executable_path.pth': b'import forbidden_bootstrap\n'})
    distribution('absolute-path', {'absolute_path.pth': str(root).encode()})
    distribution('entrypoint', entry=True)
    distribution('external-record', extra_record=[['../../Scripts/old.exe', '', '']])
    distribution('incompatible', tag='cp27-cp27m-win32')
    distribution('pip'); distribution('briefloop')
    distribution('unlocked'); names.remove('unlocked')
    distribution('wrong-version'); (old_site/'wrong_version-1.0.dist-info'/'METADATA').write_text('Name: wrong-version\nVersion: 2.0\n')
    symlink_checked = False
    try:
        info = distribution('linked')
        linked = old_site/'linked'/'__init__.py'; linked.unlink(); linked.symlink_to(old_site/'safe'/'__init__.py')
        symlink_checked = True
    except OSError:
        names.remove('linked')

    expected = {'platform': sys.platform, 'version': '.'.join(map(str, sys.version_info[:3])),
                'executable': getattr(sys, '_base_executable', sys.executable)}
    lock = '\n'.join(name+'==1.0 --hash=sha256:'+'0'*64 for name in names)
    result = copy_dependencies(source, target, lock, expected)
    assert result['reused'] == ['safe', 'relative-path'], result
    assert set(result['skipped']) == set(names)-{'safe', 'relative-path'}, result
    assert not (target/'Scripts').exists()
    assert not (target/'pyvenv.cfg').exists()
    assert (new_site/'bootstrap-marker').read_bytes() == b'fresh bootstrap'
    assert (new_site/'safe.libs'/'runtime.dll').read_bytes() == b'synthetic DLL bytes'
    assert not (new_site/'unlocked').exists()
    assert not (new_site/'entrypoint').exists()
    assert not (new_site/'executable_path.pth').exists()
    (new_site/'safe'/'__init__.py').write_bytes(b'target only change')
    assert (old_site/'safe'/'__init__.py').read_bytes() == b'VALUE = 1\n'
    for change in [{'platform':'not-this-platform'}, {'version':'0.0.0'}, {'executable':str(root/'missing-python')}]:
        try: copy_dependencies(source, target, lock, {**expected, **change})
        except ValueError: pass
        else: raise AssertionError('Changed interpreter identity accepted')
    if symlink_checked:
        alias = root/'alias'; alias.symlink_to(source, target_is_directory=True)
        try: copy_dependencies(alias, target, lock, expected)
        except ValueError: pass
        else: raise AssertionError('Linked environment root accepted')
    print(json.dumps({'reused': result['reused'], 'skipped': len(result['skipped']), 'symlinkChecked': symlink_checked}))
`;
  const result = spawnSync(python, ['-I', '-c', script], {encoding: 'utf8', timeout: 30000});
  assert.equal(result.status, 0, result.stderr || String(result.error));
  const evidence = JSON.parse(result.stdout.trim());
  assert.deepEqual(evidence.reused, ['safe', 'relative-path']);
  assert.ok(evidence.skipped >= 10);
  t.diagnostic(JSON.stringify(evidence));
});
