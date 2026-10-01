'use strict';
const path = require('node:path');

// Run with the fresh target's Python and -S: the source's .pth files must never
// execute while deciding which installed distributions are safe to copy.
// This deliberately is not a general venv mover. Packages with launchers,
// executable .pth files, or data outside site-packages stay with pip's installer.
const COPY_DEPENDENCIES = String.raw`
import base64, csv, hashlib, importlib.metadata, io, json, os, pathlib, re, stat, sys

def safe_path(root, relative):
    value = relative.replace('\\', '/')
    if not value or value.startswith('/') or ':' in value or any(p in ('', '.', '..') for p in value.split('/')):
        raise ValueError('Non-local installed path')
    target = root.joinpath(*value.split('/'))
    current = root
    for part in value.split('/'):
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Linked installed path')
    return target

def checked_root(value):
    root = pathlib.Path(value)
    if not root.is_absolute():
        raise ValueError('Absolute environment path required')
    for current in [root, *root.parents]:
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Linked environment directory')
    if not root.is_dir():
        raise ValueError('Environment directory missing')
    return root

def locked_requirements(text, Requirement, canonicalize_name):
    result = {}
    for raw in text.replace('\\\r\n', '').replace('\\\n', '').splitlines():
        value = raw.split('#', 1)[0].strip()
        if not value:
            continue
        value = re.sub(r'\s+--hash=sha256:[a-fA-F0-9]{64}(?=\s|$)', '', value)
        requirement = Requirement(value)
        pins = list(requirement.specifier)
        if requirement.url or requirement.extras or len(pins) != 1 or pins[0].operator != '==' or '*' in pins[0].version:
            raise ValueError('Unsupported dependency lock')
        if requirement.marker is None or requirement.marker.evaluate():
            name = canonicalize_name(requirement.name)
            if name in result:
                raise ValueError('Duplicate active dependency')
            result[name] = pins[0].version
    return result

def copy_dependencies(source, target, requirements, expected):
    source = checked_root(source)
    target = checked_root(target)
    if source == target or source in target.parents or target in source.parents:
        raise ValueError('Environment directories overlap')
    if sys.platform != expected['platform'] or '.'.join(map(str, sys.version_info[:3])) != expected['version']:
        raise ValueError('Interpreter identity changed')
    actual_base = os.path.normcase(os.path.realpath(getattr(sys, '_base_executable', sys.executable)))
    if actual_base != os.path.normcase(os.path.realpath(expected['executable'])):
        raise ValueError('Base interpreter changed')
    source_site = checked_root(source / 'Lib' / 'site-packages')
    target_site = checked_root(target / 'Lib' / 'site-packages')
    # -S avoids executing any .pth, including the fresh bootstrap's. pip is
    # imported only from the newly created target; its packaging handles current
    # CPython/PyPy, architecture, free-threaded and stable-ABI wheel tags.
    sys.path.insert(0, str(target_site))
    from pip._vendor.packaging.requirements import Requirement
    from pip._vendor.packaging.tags import parse_tag, sys_tags
    from pip._vendor.packaging.utils import canonicalize_name
    from pip._vendor.packaging.version import Version
    wanted = locked_requirements(requirements, Requirement, canonicalize_name)
    compatible = set(sys_tags())
    distributions = {}
    for info in source_site.iterdir():
        if not info.name.endswith('.dist-info'):
            continue
        try:
            # Check every metadata path before importlib reads it. A linked
            # METADATA/RECORD must not cause even a read outside this venv.
            for filename in ('METADATA', 'WHEEL', 'RECORD'):
                item = safe_path(source_site, info.name + '/' + filename)
                if not item.is_file():
                    raise ValueError('Metadata is not a file')
            entry_file = info / 'entry_points.txt'
            if entry_file.exists() or entry_file.is_symlink():
                safe_path(source_site, info.name + '/entry_points.txt')
            dist = importlib.metadata.PathDistribution(info)
            name = canonicalize_name(dist.metadata.get('Name', ''))
            distributions.setdefault(name, []).append(dist)
        except (ValueError, OSError, UnicodeError):
            continue
    reused, skipped = [], []
    for name, version in wanted.items():
        if name in ('briefloop', 'pip', 'setuptools', 'wheel'):
            skipped.append(name)
            continue
        try:
            matches = distributions.get(name, [])
            if len(matches) != 1:
                raise ValueError('Missing or duplicate distribution')
            dist = matches[0]
            if Version(dist.version) != Version(version):
                raise ValueError('Dependency version changed')
            if any(entry.group in ('console_scripts', 'gui_scripts') for entry in dist.entry_points):
                raise ValueError('Distribution requires generated launchers')
            wheel = dist.read_text('WHEEL') or ''
            tags = [line.split(':', 1)[1].strip() for line in wheel.splitlines() if line.startswith('Tag:')]
            if not tags or not any(parse_tag(tag) & compatible for tag in tags):
                raise ValueError('Incompatible installed wheel')
            record = dist.read_text('RECORD')
            if not record:
                raise ValueError('Installed file manifest missing')
            entries, seen = [], set()
            required_metadata = {'METADATA', 'WHEEL', 'RECORD'}
            metadata_files = set()
            for row in csv.reader(io.StringIO(record)):
                if len(row) != 3:
                    raise ValueError('Malformed installed file manifest')
                relative, digest, size = row
                src = safe_path(source_site, relative)
                if not src.is_file():
                    raise ValueError('Installed file missing')
                # Python regenerates these with the target's path/cache tag.
                if src.suffix == '.pyc':
                    continue
                normalized = relative.replace('\\', '/')
                key = normalized.casefold()
                if key in seen:
                    raise ValueError('Duplicate installed path')
                seen.add(key)
                dst = target_site.joinpath(*normalized.split('/'))
                if dst.exists() or dst.is_symlink():
                    raise ValueError('Fresh bootstrap or dependency collision')
                # A copied .pth can add only an existing local relative path.
                # Executable hooks such as pywin32's must be installed by pip.
                if src.suffix.lower() == '.pth':
                    for raw in src.read_text(encoding='utf-8-sig').splitlines():
                        line = raw.strip()
                        if line and not line.startswith('#'):
                            if line.startswith(('import ', 'import\t')):
                                raise ValueError('Executable path hook')
                            safe_path(source_site, line)
                data = src.read_bytes()
                is_record = src.name == 'RECORD' and src.parent == pathlib.Path(dist._path)
                if not digest and not is_record:
                    raise ValueError('Unverified installed file')
                if digest:
                    algorithm, encoded = digest.split('=', 1)
                    if algorithm not in ('sha256', 'sha384', 'sha512'):
                        raise ValueError('Unsupported installed file hash')
                    actual = base64.urlsafe_b64encode(hashlib.new(algorithm, data).digest()).decode().rstrip('=')
                    if actual != encoded or not size or len(data) != int(size):
                        raise ValueError('Installed file changed')
                if src.parent == pathlib.Path(dist._path):
                    metadata_files.add(src.name)
                # Keep the verified bytes through the copy rather than opening
                # the source again after its integrity check.
                entries.append((dst, data))
            if not required_metadata <= metadata_files:
                raise ValueError('Unverified distribution metadata')
        except (ValueError, OSError, UnicodeError, KeyError):
            skipped.append(name)
            continue
        # Independent ordinary copies preserve rollback; no hard links, venv
        # executables, old scripts or unrecorded files cross into the new venv.
        for dst, data in entries:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
        reused.append(name)
    return {'reused': reused, 'skipped': skipped}

if __name__ == '__main__':
    source, target, lock_path, expected_json = sys.argv[1:]
    text = pathlib.Path(lock_path).read_text(encoding='utf-8')
    print(json.dumps(copy_dependencies(source, target, text, json.loads(expected_json))))
`;

// The caller owns creation/removal of the fresh target and all activation. A
// failed helper is a normal fresh-install fallback unless cancellation/owned
// process cleanup failed. Successful partial reuse still needs a full lock
// check, pip installation of missing packages, imports, and pip check.
async function reuseWindowsDependencies({source, target, previous, python, manifest,
                                         platform, arch, run, signal}) {
  const skipped = {reused: [], skipped: [], eligible: false};
  const normalize = value => path.win32.normalize(value || '').toLowerCase();
  if (platform !== 'win32' || previous?.schema !== 1 || previous.platform !== platform || previous.arch !== arch
      || typeof previous.requirementsSha256 !== 'string' || typeof manifest.requirements_sha256 !== 'string'
      || !/^[a-f0-9]{64}$/i.test(previous.requirementsSha256)
      || previous.requirementsSha256.toLowerCase() !== manifest.requirements_sha256.toLowerCase()
      || previous.pythonVersion !== python.version || typeof previous.hostPython !== 'string'
      || normalize(previous.hostPython) !== normalize(python.executable)) return skipped;
  const executable = path.join(target, 'Scripts', 'python.exe');
  const expected = JSON.stringify({platform, version: python.version, executable: python.executable});
  const result = await run(executable, ['-I', '-S', '-c', COPY_DEPENDENCIES, source, target, manifest.requirementsPath, expected], signal, 120000);
  const value = JSON.parse(result.stdout.trim());
  if (!Array.isArray(value.reused) || !Array.isArray(value.skipped)
      || ![...value.reused, ...value.skipped].every(name => typeof name === 'string')) throw Error('Invalid dependency reuse result');
  return {...value, eligible: true};
}

module.exports = {reuseWindowsDependencies, COPY_DEPENDENCIES};
