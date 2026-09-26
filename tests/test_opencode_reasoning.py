import pytest

from briefloop.runtime_reasoning import options


class NoBridge:
    def call(self, *args, **kwargs):
        raise AssertionError('OpenCode variants must use the owned provider catalog, not another CLI')


class Catalog:
    def list_models(self):
        return [{'id': 'fixture/reasoner', 'variants': ['low', 'high', 'turbo']},
                {'id': 'fixture/plain', 'variants': []}, {'id': 'fixture/unknown'}]


def test_opencode_uses_selected_model_variants_and_distinguishes_missing_metadata(tmp_path):
    result = options('opencode', 'fixture/reasoner', tmp_path, NoBridge(), opencode=Catalog())
    assert [row['id'] for row in result['options']] == ['low', 'high', 'turbo']
    assert result['availability'] == 'advertised' and result['source'] == 'host'
    plain = options('opencode', 'fixture/plain', tmp_path, NoBridge(), opencode=Catalog())
    unknown = options('opencode', 'fixture/unknown', tmp_path, NoBridge(), opencode=Catalog())
    assert plain['options'] == unknown['options'] == []
    assert plain['availability'] == 'no_variants'
    assert unknown['availability'] == 'not_advertised'
    with pytest.raises(ValueError, match='未在当前目录'):
        options('opencode', 'fixture/missing', tmp_path, NoBridge(), opencode=Catalog())


def test_opencode_default_does_not_invent_effort_or_hide_catalog_failure(tmp_path):
    class FailedCatalog:
        def list_models(self):
            raise RuntimeError('provider metadata unavailable')

    result = options('opencode', 'default', tmp_path, NoBridge(), opencode=FailedCatalog())
    assert result['options'] == [] and result['availability'] == 'select_model'
    with pytest.raises(RuntimeError, match='provider metadata unavailable'):
        options('opencode', 'fixture/reasoner', tmp_path, NoBridge(), opencode=FailedCatalog())
    with pytest.raises(ValueError, match='模型目录不可用'):
        options('opencode', 'fixture/reasoner', tmp_path, NoBridge())
