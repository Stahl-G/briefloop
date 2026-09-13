"""Archive trust boundaries and locked Windows runtime compatibility."""
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import zipfile

import pytest

SCRIPTS=Path(__file__).resolve().parents[1]/'desktop/electron/scripts'
spec=importlib.util.spec_from_file_location('prepare_windows',SCRIPTS/'prepare-runtime-windows.py')
prepare=importlib.util.module_from_spec(spec);spec.loader.exec_module(prepare)


@pytest.mark.parametrize('name',['../escape','C:/escape','dir\\escape','data:stream','CON.txt','trailing.'])
def test_archive_paths_reject_windows_escape_and_aliases(tmp_path,name):
    archive=tmp_path/'bad.zip'
    item=zipfile.ZipInfo('entry');item.filename=name
    with zipfile.ZipFile(archive,'w') as package:package.writestr(item,b'bad')
    with pytest.raises(ValueError):prepare.unpack(archive,tmp_path/'output')
    assert not (tmp_path/'escape').exists()


def test_tar_links_rejected_and_unicode_regular_files_survive(tmp_path):
    archive=tmp_path/'test.tar.gz'
    with tarfile.open(archive,'w:gz') as package:
        item=tarfile.TarInfo('python/中文 空格.txt');data='有效内容'.encode('utf-8');item.size=len(data)
        package.addfile(item,io.BytesIO(data))
    prepare.unpack(archive,tmp_path/'valid')
    assert (tmp_path/'valid/python/中文 空格.txt').read_text(encoding='utf-8')=='有效内容'
    with tarfile.open(archive,'w:gz') as package:
        item=tarfile.TarInfo('python/link');item.type=tarfile.SYMTYPE;item.linkname='../../outside';package.addfile(item)
    with pytest.raises(ValueError):prepare.unpack(archive,tmp_path/'invalid')


def test_windows_lock_contains_only_pinned_binary_artifacts():
    lock=json.loads((SCRIPTS/'runtime-lock-windows.json').read_text(encoding='utf-8'))
    assert lock['platform']=='windows-x64'
    assert any(a['name'].lower()=='pywin32' for a in lock['python_wheels'])
    for item in [lock[k] for k in ('python','node','python_licenses')]+lock['python_wheels']:
        assert item['url'].startswith('https://') and len(bytes.fromhex(item['sha256']))==32
    assert all(a['filename'].endswith('.whl') and ('win_amd64' in a['filename'] or 'none-any' in a['filename']) for a in lock['python_wheels'])
