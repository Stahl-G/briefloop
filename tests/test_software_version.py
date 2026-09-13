import io
import json
from urllib.error import HTTPError
from briefloop import software_version as version


def test_desktop_identity_follows_active_environment(tmp_path,monkeypatch):
    environment=tmp_path/'environments'/'test-environment'
    environment.mkdir(parents=True)
    (environment.parent/'active.json').write_text(json.dumps({'environmentId':environment.name,'version':version.__version__,'sha256':'a'*64}))
    monkeypatch.setattr(version.sys,'prefix',str(environment))
    result=version.runtime_info()
    assert result['installation']=='desktop'
    assert result['version']==version.__version__
    assert result['update_command'] is None
    assert result['build']=='a'*12


def test_check_ignores_prerelease_yanked_and_reports_channel_lag(monkeypatch):
    monkeypatch.setattr(version,'_cache',None)
    def fetch(request,timeout):
        assert request.full_url=='https://pypi.org/pypi/briefloop/json'
        return io.BytesIO(json.dumps({'releases':{'0.18.0':[{}],'0.21.0':[{'yanked':True}],'0.22.0rc1':[{}]}}).encode())
    monkeypatch.setattr(version,'urlopen',fetch)
    result=version.check_update({'version':'0.20.0','installation':'desktop'})
    assert result['state']=='ahead'
    assert result['releaseVersion']=='0.18.0'


def test_check_provider_error_is_visible_and_retryable(monkeypatch):
    monkeypatch.setattr(version,'_cache',None)
    def fetch(*args,**kwargs):raise HTTPError(version.PYPI,503,'Unavailable',{},None)
    monkeypatch.setattr(version,'urlopen',fetch)
    result=version.check_update({'version':'0.20.0'})
    assert result['state']=='error' and result['retryable']
    assert result['error']['code']=='http_503'
    assert '503' in result['error']['message']
