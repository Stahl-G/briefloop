"""Run inside the bundled Python during build, never on first application launch."""
from importlib import metadata
from pathlib import Path
import hashlib
import base64
import csv
import json
import re
import shutil
import sys

root = Path(sys.argv[1]).resolve()
python = root/'python'
licenses = root/'licenses/python-packages'
licenses.mkdir(parents=True, exist_ok=True)
records = []
record_files = []
for dist in sorted(metadata.distributions(), key=lambda d: d.metadata['Name'].lower()):
    name = dist.metadata['Name']
    if dist.read_text('direct_url.json') is not None:
        direct = Path(dist._path)/'direct_url.json'
        direct.unlink()
    record_files.append(Path(dist._path)/'RECORD')
    destination = licenses/(name+'-'+dist.version)
    if destination.exists():
        shutil.rmtree(destination)
    files = []
    for item in dist.files or []:
        path = dist.locate_file(item).resolve()
        if not path.is_relative_to(python) or not path.is_file():
            continue
        parts = Path(item).parts
        if not any(re.match(r'^(licen[cs]es?|copying|notice|authors)([._-]|$)', p, re.I) for p in parts):
            continue
        safe = Path(*[p for p in parts if p not in ('.', '..')])
        target = destination/safe
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        files.append({'file': str(target.relative_to(root)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    if not files:
        raise ValueError('No license text found for installed dependency '+name)
    records.append({'name': name, 'version': dist.version, 'licenses': files})
    for entry in dist.entry_points:
        if entry.group != 'console_scripts':
            continue
        if Path(entry.name).name != entry.name:
            raise ValueError('Unsafe console entry point name')
        wrapper = python/'bin'/entry.name
        wrapper.write_text("#!/bin/sh\n'''exec' \"$(dirname \"$0\")/python3\" \"$0\" \"$@\"\n' '''\n"
                           "from importlib.metadata import distribution\n"
                           f"entry = next(e for e in distribution({name!r}).entry_points if e.group == 'console_scripts' and e.name == {entry.name!r})\n"
                           "raise SystemExit(entry.load()())\n")
        wrapper.chmod(0o755)
# pip also ships versioned entry points outside its entry-point metadata.
for script in (python/'bin').glob('pip*'):
    if script.is_file() and not script.is_symlink():
        script.write_text("#!/bin/sh\nexec \"$(dirname \"$0\")/python3\" -m pip \"$@\"\n")
        script.chmod(0o755)
(root/'licenses/python-packages.json').write_text(json.dumps(records, indent=2)+'\n')
# These caches refer to temporary build paths and are unnecessary at launch.
for cache in list(python.rglob('__pycache__')):
    shutil.rmtree(cache)
print('Preserved license records:', len(records))
# Refresh RECORD for normalized launchers and removed generated metadata/caches.
for record in record_files:
    if not record.is_file():
        continue
    rows = []
    with record.open(newline='') as stream:
        for row in csv.reader(stream):
            file = record.parent.parent/row[0]
            if not file.is_file():
                continue
            if file.resolve() == record.resolve():
                rows.append([row[0], '', ''])
            else:
                data = file.read_bytes()
                digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=')
                rows.append([row[0], 'sha256='+digest, str(len(data))])
    with record.open('w', newline='') as stream:
        csv.writer(stream).writerows(rows)
