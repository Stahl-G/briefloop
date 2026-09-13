import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import tomllib

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('check_versions', ROOT/'scripts/check_versions.py')
checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)


def args(root, **values):
    return argparse.Namespace(root=root,cli=None,mac_app=None,windows_app=None,wheel=None,pypi=False,
                              platform_report=[],require_all=False,**values)


def test_source_follows_current_metadata_and_missing_platforms_never_pass(tmp_path):
    expected=tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    result,status=checker.check(args(ROOT))
    assert result['expected']==expected and result['checks']['source']['status']=='match'
    assert status==0 and result['all_platforms_consistent'] is False
    a=args(ROOT);a.require_all=True
    assert checker.check(a)[1]==1
    for filename in ('VERSION','pyproject.toml','src/briefloop/__init__.py','desktop/electron/package.json',
                     'desktop/electron/package-lock.json','src/briefloop/static/index.html'):
        target=tmp_path/filename;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/filename,target)
    target=tmp_path/'desktop/electron/package.json';data=json.loads(target.read_text(encoding='utf-8'))
    numbers=expected.split('.');numbers[-1]=str(int(numbers[-1])+1);data['version']='.'.join(numbers)
    target.write_text(json.dumps(data))
    result,status=checker.check(args(tmp_path))
    assert status==1 and result['checks']['source']['status']=='mismatch'


def test_desktop_must_use_shared_wheel_and_failed_remote_evidence_stays_failed(tmp_path):
    import hashlib
    import zipfile
    import pytest
    expected=tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']
    base=tmp_path/'backend';base.mkdir()
    wheel=base/'briefloop-test.whl'
    with zipfile.ZipFile(wheel,'w') as z:
        z.writestr('briefloop-test.dist-info/METADATA',f'Name: briefloop\nVersion: {expected}\n')
    digest=hashlib.sha256(wheel.read_bytes()).hexdigest()
    (base/'manifest.json').write_text(json.dumps({'version':expected,'wheel':wheel.name,'sha256':digest}))
    assert checker.backend_versions(tmp_path,digest)['backend_wheel']==expected
    with pytest.raises(ValueError,match='shared release wheel'):
        checker.backend_versions(tmp_path,'0'*64)
    report=tmp_path/'windows.json'
    a=args(ROOT);a.wheel=wheel;a.platform_report=[report]
    for state,hash_value in [('error',digest),('match','0'*64),('match',None)]:
        report.write_text(json.dumps({'expected':expected,'checks':{'windows':{'status':state,'versions':{'app':expected},'release_wheel_sha256':hash_value}}}))
        result,status=checker.check(a)
        assert status==1 and result['checks']['windows']['status']=='error'
    report.write_text(json.dumps({'expected':expected,'checks':{'windows':{'status':'match','versions':{'app':expected},'release_wheel_sha256':digest}}}))
    result,status=checker.check(a)
    assert status==0 and result['checks']['windows']['release_wheel_sha256']==digest
