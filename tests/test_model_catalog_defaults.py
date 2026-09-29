import pytest

from briefloop.harness import HarnessManager
from briefloop.runtime_reasoning import options
from briefloop.store import Store


def test_new_workspace_has_no_factory_model_and_preserves_explicit_selection(tmp_path):
    store = Store(tmp_path)
    assert store.settings()['model'] == ''
    assert store.settings()['reasoning_effort'] is None
    with pytest.raises(ValueError, match='选择'):
        store.runtime_config()
    store.update_settings({'model': 'provider-chosen-model', 'reasoning_effort': 'high'})
    reopened = Store(tmp_path)
    assert reopened.runtime_config()['model'] == 'provider-chosen-model'
    assert reopened.runtime_config()['reasoning_effort'] == 'high'
    # A direct host session may delegate to the host's real default; no concrete
    # product-selected model or reasoning level is silently sent.
    assert HarnessManager._config({})['model'] == 'default'
    assert HarnessManager._config({})['effort'] is None


def test_host_without_model_capability_api_does_not_guess_effort(tmp_path):
    class NoProbe:
        def call(self, *args, **kwargs):
            raise AssertionError('No capability endpoint is advertised')

    for backend in ('claude', 'pi', 'codebuddy'):
        result = options(backend, 'provider-new-model', tmp_path, NoProbe())
        assert result['options'] == []
        assert result['availability'] == 'not_advertised'

    class LocalMetadata:
        def list_models(self):
            return [{'id': 'fixture/model', 'thinking_levels': ['low', 'high'],
                     'thinking_levels_source': 'execution_metadata'}]

    result = options('briefloop-native', 'fixture/model', tmp_path, NoProbe(), LocalMetadata())
    assert result['options'] == []
    assert result['availability'] == 'not_advertised'
