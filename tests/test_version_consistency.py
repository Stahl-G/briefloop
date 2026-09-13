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
