from types import SimpleNamespace

import pytest

from briefloop import opencode_version, review_capability

pytestmark = pytest.mark.real_review_capabilities


@pytest.mark.parametrize('major', [1, 2, None, 3])
def test_opencode_standard_requires_supported_version_but_never_claims_strict(monkeypatch, major):
    monkeypatch.setattr(review_capability, '_opencode_major', lambda: major)
    assert not review_capability.restricted_review('opencode')
    assert ('opencode' in [row['id'] for row in review_capability.review_choices()]) == (major in (1,2))
    with pytest.raises(review_capability.ReviewBackendUnsupported, match='严格'):
        review_capability.require_for_review('opencode','strict')
    if major in (1,2):
        review_capability.require_for_fact_check('opencode',review_mode='standard')
        assert review_capability.delivery_blocker('opencode') is None
    else:
        with pytest.raises(review_capability.ReviewBackendUnsupported):
            review_capability.require_for_fact_check('opencode')
    native = {'backend': 'briefloop-native', 'model': 'fixture/reviewer', 'model_variant': 'high'}
    review_capability.require_for_fact_check('opencode', native,'strict')
    assert review_capability.delivery_blocker('opencode',native,'strict') is None


def test_probe_caches_unchanged_install_and_rechecks_replaced_binary(tmp_path, monkeypatch):
    binary = tmp_path / 'opencode'
    binary.write_text('first')
    monkeypatch.setattr(opencode_version, 'find', lambda _: str(binary))
    monkeypatch.setattr(opencode_version, 'cli_command', lambda argv: argv)
    monkeypatch.setattr(opencode_version, '_cache', {})
    probes = []
    response = SimpleNamespace(stdout='1.18.31\n')
    def probe(argv, **kwargs):
        probes.append(argv)
        assert argv == [str(binary), '--version']
        assert kwargs['timeout'] == 3
        return response
    monkeypatch.setattr(opencode_version.subprocess, 'run', probe)
    assert opencode_version.installed_major() == 1
    assert opencode_version.installed_major() == 1 and len(probes) == 1
    binary.write_text('replacement binary')
    response.stdout = 'opencode v2.0.14\n'
    assert opencode_version.installed_major() == 2
    assert len(probes) == 2
    binary.write_text('not a recognized version')
    response.stdout = 'unexpected banner\n'
    assert opencode_version.installed_major() is None
    assert opencode_version.installed_major() is None and len(probes) == 3


def test_missing_or_failed_probe_never_assumes_v1(tmp_path, monkeypatch):
    monkeypatch.setattr(opencode_version, 'find', lambda _: None)
    assert opencode_version.installed_major() is None
    binary = tmp_path / 'opencode'
    binary.write_text('fixture')
    monkeypatch.setattr(opencode_version, 'find', lambda _: str(binary))
    monkeypatch.setattr(opencode_version, 'cli_command', lambda argv: argv)
    monkeypatch.setattr(opencode_version, '_cache', {})
    probes = []
    clock = [100.0]
    monkeypatch.setattr(opencode_version.time, 'monotonic', lambda: clock[0])
    def timeout(argv, **kwargs):
        probes.append(argv)
        if len(probes) == 1:
            raise opencode_version.subprocess.TimeoutExpired(argv, 3)
        return SimpleNamespace(stdout='v1.18.31\n' if len(probes) == 2 else 'opencode v2.0.14\n')
    monkeypatch.setattr(opencode_version.subprocess, 'run', timeout)
    assert opencode_version.installed_major() is None
    assert opencode_version.installed_major() is None and len(probes) == 1
    clock[0] += opencode_version._FAILURE_TTL + 1
    assert opencode_version.installed_major() == 1
    assert opencode_version.installed_major() == 1 and len(probes) == 2
    # The same wrapper can load a replaced inner binary: refresh even though
    # its own path/stat stayed unchanged, without probing on every state read.
    clock[0] += opencode_version._SUCCESS_TTL + 1
    assert opencode_version.installed_major() == 2 and len(probes) == 3
