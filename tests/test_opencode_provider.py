import pytest
from briefloop.backends.opencode_server import OpencodeServerClient, OpencodeError
from briefloop.opencode_harness import OpencodeHarness
from briefloop.store import Store


def test_provider_native_calls_keep_key_out_of_configuration_and_result(tmp_path):
    client=object.__new__(OpencodeServerClient)
    calls=[]
    client._request=lambda method,path,body=None:calls.append((method,path,body))
    result=client.configure_provider(tmp_path,'deepseek','custom-model','https://api.deepseek.com','test-secret')
    assert calls[0][0:2]==('PATCH','/global/config')
    assert 'test-secret' not in str(calls[0])
    assert calls[1]==('PUT','/auth/deepseek',{'type':'api','key':'test-secret'})
    assert result['model']=='deepseek/custom-model' and result['key_saved']
    assert 'test-secret' not in str(result)
    def fail(*args):raise OpencodeError('echo test-secret',status=400)
    client._request=fail
    with pytest.raises(ValueError) as error:
        client.configure_provider(tmp_path,'deepseek','custom-model','https://api.deepseek.com','test-secret')
    assert 'test-secret' not in str(error.value)


def test_provider_validation_busy_and_cache_invalidation(tmp_path):
    manager=OpencodeHarness(Store(tmp_path))
    class Client:
        def configure_provider(self,*args):return {'model':args[1]+'/'+args[2]}
    manager._client=lambda:Client()
    body={'provider':'deepseek','model':'custom-model','base_url':'https://api.deepseek.com'}
    manager._models_cache=['old'];manager._busy.add('active')
    with pytest.raises(ValueError,match='运行'):manager.configure_provider(body)
    manager._busy.clear()
    assert manager.configure_provider(body)['model']=='deepseek/custom-model'
    assert manager._models_cache is None
    with pytest.raises(ValueError,match='Base URL'):manager.configure_provider({**body,'base_url':'https://secret@api.example.com'})


def test_custom_image_capability_is_explicit_and_omission_preserves_existing(tmp_path):
    client=object.__new__(OpencodeServerClient);calls=[]
    client._request=lambda method,path,body=None:calls.append((method,path,body))
    client.configure_provider(tmp_path,'provider','custom','https://example.test',supports_images=True)
    model=calls[0][2]['provider']['provider']['models']['custom']
    assert model['attachment'] is True and model['modalities']['input']==['text','image']
    calls.clear();client.configure_provider(tmp_path,'provider','custom','https://example.test')
    assert calls[0][2]['provider']['provider']['models']['custom']=={'name':'custom'}
