"""Build a relocatable Windows x64 runtime; installation is build-time only."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import base64
import csv
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.parse
import urllib.request
import zipfile

HERE=Path(__file__).resolve().parent
DESKTOP=HERE.parent
REPO=DESKTOP.parents[1]
CACHE=DESKTOP/'.cache/windows-runtime'
OUTPUT=DESKTOP/'runtime/windows-x64'


def run(args, **kwargs):
    return subprocess.run(list(map(str,args)),check=True,**kwargs)


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def safe_relative(name):
    # Windows interprets backslashes, drive prefixes, ADS, device names and
    # trailing dots specially, even when the archive was created on Unix.
    path=PurePosixPath(name)
    if not name or '\\' in name or path.is_absolute() or any(
        part in ('.','..') or ':' in part or part.rstrip(' .')!=part
        or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?',part)
        for part in path.parts):raise ValueError('Unsafe archive path: '+name)
    return Path(*path.parts)


def unpack(archive,destination):
    destination=destination.resolve();seen=set()
    def target(name):
        relative=safe_relative(name.rstrip('/'))
        key=relative.as_posix().casefold()
        if key in seen:raise ValueError('Duplicate archive path: '+name)
        seen.add(key)
        path=destination/relative
        if not path.resolve().is_relative_to(destination):raise ValueError('Archive path escapes destination')
        return path
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as package:
            entries=[]
            for item in package.infolist():
                if stat.S_ISLNK(item.external_attr>>16):raise ValueError('Archive links are not permitted')
                entries.append((item,target(item.orig_filename)))
            for item,path in entries:
                if item.is_dir():path.mkdir(parents=True,exist_ok=True)
                else:
                    path.parent.mkdir(parents=True,exist_ok=True)
                    with package.open(item) as src,path.open('wb') as dst:shutil.copyfileobj(src,dst)
    else:
        with tarfile.open(archive) as package:
            entries=[]
            for item in package:
                if not (item.isfile() or item.isdir()):raise ValueError('Archive links/special files are not permitted')
                entries.append((item,target(item.name)))
            for item,path in entries:
                if item.isdir():path.mkdir(parents=True,exist_ok=True)
                else:
                    path.parent.mkdir(parents=True,exist_ok=True)
                    with package.extractfile(item) as src,path.open('wb') as dst:shutil.copyfileobj(src,dst)


def fetch(item,offline=False):
    filename=item['filename']
    if safe_relative(filename).name!=filename:raise ValueError('Unsafe download filename')
    target=CACHE/filename
    if target.exists() and sha(target)==item['sha256']:return target
    if offline:raise ValueError('Missing verified offline artifact: '+filename)
    if urllib.parse.urlsplit(item['url']).scheme!='https':raise ValueError('HTTPS downloads required')
    temporary=target.with_name(filename+'.part')
    with urllib.request.urlopen(item['url'],timeout=180) as src,temporary.open('wb') as dst:
        if urllib.parse.urlsplit(src.url).scheme!='https':raise ValueError('HTTPS redirects required')
        shutil.copyfileobj(src,dst)
    if sha(temporary)!=item['sha256']:
        temporary.unlink();raise ValueError('Checksum mismatch: '+filename)
    temporary.replace(target)
    print('Verified',filename,flush=True)
    return target


def remove_generated(path,within):
    path=path.resolve();within=within.resolve()
    if path==within or not path.is_relative_to(within):raise ValueError('Unsafe generated directory cleanup')
    if path.exists():shutil.rmtree(path)


def python_licenses(archive,destination):
    # Windows' bundled bsdtar supports zstd. Read selected entries to stdout;
    # never let an external extractor choose filesystem destinations.
    names=subprocess.check_output(['tar','-tf',str(archive)],encoding='utf-8').splitlines()
    selected=[name for name in names if name=='python/PYTHON.json' or name.startswith('python/licenses/') and not name.endswith('/')]
    if 'python/PYTHON.json' not in selected or len(selected)<2:raise ValueError('Python licenses missing')
    for name in selected:
        relative=safe_relative(name).relative_to('python')
        path=destination/relative;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(subprocess.check_output(['tar','-xOf',str(archive),name]))


def normalize_metadata(root):
    python=(root/'python').resolve();records=[]
    for dist in sorted(metadata.distributions(),key=lambda d:d.metadata['Name'].lower()):
        name=dist.metadata['Name'];record=Path(dist._path)/'RECORD'
        if not record.resolve().is_relative_to(python):raise ValueError('Host package leaked into runtime')
        direct=Path(dist._path)/'direct_url.json'
        if direct.exists():direct.unlink()
        licenses=[]
        for item in dist.files or []:
            source=dist.locate_file(item).resolve()
            if not source.is_relative_to(python) or not source.is_file():continue
            if not any(re.match(r'^(licen[cs]es?|copying|notice|authors)([._-]|$)',p,re.I) for p in Path(item).parts):continue
            safe=Path(*[p for p in Path(item).parts if p not in ('.','..')])
            target=root/'licenses/python-packages'/(name+'-'+dist.version)/safe
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
            licenses.append({'file':target.relative_to(root).as_posix(),'sha256':sha(target)})
        if not licenses:raise ValueError('Missing dependency license: '+name)
        records.append({'name':name,'version':dist.version,'licenses':licenses})
        # distlib's Windows EXEs embed a build-time absolute interpreter path.
        # Replace them with relative .cmd launchers and include them in RECORD.
        added=[]
        for entry in dist.entry_points:
            if entry.group!='console_scripts':continue
            if not re.fullmatch(r'[A-Za-z0-9_.-]+',entry.name):raise ValueError('Unsafe entry point')
            launcher=python/'Scripts'/(entry.name+'.cmd');launcher.parent.mkdir(exist_ok=True)
            code=(f"from importlib.metadata import distribution; e=next(e for e in distribution({name!r}).entry_points if e.group=='console_scripts' and e.name=={entry.name!r}); raise SystemExit(e.load()())")
            launcher.write_text('@echo off\n"%~dp0..\\python.exe" -I -c "'+code+'" %*\n',encoding='utf-8')
            added.append(launcher)
        with record.open(encoding='utf-8',newline='') as stream:rows=list(csv.reader(stream))
        for row in rows:
            path=(record.parent.parent/row[0]).resolve()
            if path.is_relative_to(python/'Scripts') and path.suffix.lower() in ('.exe','.py') and path.is_file():path.unlink()
        rows.extend([os.path.relpath(path,record.parent.parent).replace('\\','/'),'',''] for path in added)
        clean=[]
        for row in rows:
            path=(record.parent.parent/row[0]).resolve()
            if not path.is_relative_to(python):raise ValueError('Installed RECORD escapes runtime')
            if not path.is_file():continue
            if path==record.resolve():clean.append([row[0],'','']);continue
            data=path.read_bytes();digest=base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode('ascii').rstrip('=')
            clean.append([row[0],'sha256='+digest,str(len(data))])
        with record.open('w',encoding='utf-8',newline='') as stream:csv.writer(stream).writerows(clean)
    (root/'licenses/python-packages.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def prepare(offline=False):
    if sys.version_info<(3,11) or os.name!='nt' or platform.machine().lower() not in ('amd64','x86_64'):
        raise SystemExit('Build on Windows x64 with Python 3.11+')
    lock=json.loads((HERE/'runtime-lock-windows.json').read_text(encoding='utf-8'))
    project=tomllib.loads((REPO/'pyproject.toml').read_text(encoding='utf-8'))
    if project['project']['dependencies']+project['build-system']['requires']!=lock['python_requirements']:
        raise ValueError('Dependencies changed; refresh/review Windows runtime lock')
    CACHE.mkdir(parents=True,exist_ok=True);OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    items=[lock[k] for k in ('python','node','python_licenses')]+lock['python_wheels']
    with ThreadPoolExecutor(max_workers=4) as pool:downloads=list(pool.map(lambda item:fetch(item,offline),items))
    with tempfile.TemporaryDirectory(prefix='.windows-build-',dir=OUTPUT.parent) as temporary:
        stage=Path(temporary)/'bundle';stage.mkdir()
        unpack(downloads[0],stage);unpack(downloads[1],stage)
        (stage/('node-v'+lock['node']['version']+'-win-x64')).rename(stage/'node')
        for item in (stage/'node').iterdir():
            if item.name in ('node.exe','LICENSE'):continue
            if item.is_dir():remove_generated(item,stage)
            else:item.unlink()
        py=stage/'python/python.exe';env=os.environ.copy()
        for key in ('PYTHONPATH','PYTHONHOME','NODE_PATH'):env.pop(key,None)
        env.update(PYTHONDONTWRITEBYTECODE='1',PIP_DISABLE_PIP_VERSION_CHECK='1',PIP_NO_CACHE_DIR='1',PIP_NO_INDEX='1',PYTHONUTF8='1')
        wheels=Path(temporary)/'wheels';wheels.mkdir()
        for artifact in downloads[3:]:shutil.copy2(artifact,wheels/artifact.name)
        requirements=Path(temporary)/'requirements.txt'
        requirements.write_text(''.join(f"{a['name']}=={a['version']} --hash=sha256:{a['sha256']}\n" for a in lock['python_wheels']),encoding='utf-8')
        run([py,'-I','-m','pip','install','--no-compile','--no-index','--find-links',wheels,'--require-hashes','-r',requirements],env=env)
        source=Path(temporary)/'source';source.mkdir()
        shutil.copytree(REPO/'src',source/'src',ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.egg-info'))
        for name in ('pyproject.toml','README.md','LICENSE','THIRD_PARTY_NOTICES.md','MANIFEST.in'):
            if (REPO/name).is_file():shutil.copy2(REPO/name,source/name)
        built=Path(temporary)/'business-wheel';built.mkdir()
        run([py,'-I','-m','pip','wheel','--no-index','--no-deps','--no-build-isolation','--wheel-dir',built,source],env=env)
        business=list(built.glob('briefloop-*.whl'))
        if len(business)!=1:raise ValueError('Expected one current BriefLoop wheel')
        run([py,'-I','-m','pip','install','--no-compile','--no-index','--no-deps',business[0]],env=env)
        run([py,'-I','-m','pip','check'],env=env)
        licenses=stage/'licenses';licenses.mkdir()
        python_licenses(downloads[2],licenses/'python-distribution')
        shutil.copy2(stage/'node/LICENSE',licenses/'NODE-LICENSE.txt')
        run([py,'-I',__file__,'--metadata',stage],env=env)
        manifest={**lock,'briefloop_wheel':{'filename':business[0].name,'sha256':sha(business[0])},'source_version':project['project']['version']}
        (stage/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        run([sys.executable,'-X','utf8',HERE/'verify-runtime-windows.py',stage],env=env)
        backup=OUTPUT.with_name('.windows-x64-previous')
        if backup.exists():raise ValueError('Inspect previous runtime backup before rebuilding')
        if OUTPUT.exists():OUTPUT.rename(backup)
        try:stage.rename(OUTPUT)
        except BaseException:
            if backup.exists():backup.rename(OUTPUT)
            raise
        remove_generated(backup,OUTPUT.parent)
    print('Prepared runtime:',OUTPUT,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--offline',action='store_true');parser.add_argument('--metadata',type=Path)
    args=parser.parse_args()
    if args.metadata:normalize_metadata(args.metadata.resolve())
    else:prepare(args.offline)
