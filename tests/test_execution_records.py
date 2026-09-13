import json
from briefloop.chat_store import ChatStore
from briefloop.execution_records import journal_tool,sanitize
from briefloop.store import Store


def test_http_credentials_are_removed_from_persisted_tool_records(tmp_path):
    store=Store(tmp_path);chat=ChatStore(store);session=chat.create('Synthetic',{},store.root)
    variants=[
        ("curl -H 'Cookie: sessionid=fake_cookie_for_test' https://example.invalid",
         'Authorization: Basic fake_basic_for_test\nCookie: sessionid=fake_cookie_for_test\nRevenue: 12'),
        ('curl --header "authorization: Digest username=\"fake_user_for_test\", response=\"fake_digest_for_test\""',
         '< SeT-CoOkIe: session=fake_session_for_test; Secure; HttpOnly\r\nRevenue: 12'),
        ('PROXY_AUTHORIZATION="Basic fake_proxy_for_test" python report.py',
         '{"Proxy-Authorization": "Basic fake_json_for_test"}\nRevenue: 12'),
        ('python report.py',r'{\"Authorization\": \"Basic fake_escaped_for_test\"}'+'\nRevenue: 12'),
        ('python report.py','Authorization: Digest\n response=fake_folded_for_test\nRevenue: 12'),
    ]
    for index,(command,output) in enumerate(variants):
        journal_tool(chat,session['id'],'turn',str(index),'bash',{'command':command},output,status='completed',
                     native_session='native',native_message_id='native-message',native_created_at=1234)
    for row in store.rows("SELECT data FROM chat_events WHERE kind='tool/record'"):
        record=json.loads(row['data'])['record']
        assert 'fake_' not in json.dumps(record) and record['redacted'] is True
        assert 'Revenue: 12' in record['output']
        assert record['native_message_id']=='native-message' and record['native_created_at']==1234
        assert sanitize(record)==record


def test_new_packet_redacts_legacy_record_without_rewriting_journal(tmp_path):
    from briefloop.review import build_packet
    store=Store(tmp_path);chat=ChatStore(store);session=chat.create('Synthetic legacy',{},store.root)
    source=store.add_source('Source','Revenue 12 million USD.')
    run=store.create_run({'title':'Report','objective':'Explain'},[source['id']])
    brief=store.publish(run['id'],{'title':'Report','markdown':'Revenue 12 million USD.'})
    job=store.enqueue('generate',{'run_id':run['id']})
    message=chat.message(session['id'],'Report',status='completed',turn_id='turn')
    chat.event(session['id'],'job/attached',{'jobId':job['id']})
    store.event(job['id'],'runtime_started',{'session_id':session['id'],'message_id':message['id']})
    # Simulate only the old vulnerable journal format, with a fake credential.
    legacy={'tool_id':'tool','tool':'bash','input':{},'output':'Cookie: fake_legacy_for_test\nRevenue: 12',
            'status':'completed','redacted':False,'record_hash':'synthetic-old-hash'}
    chat.event(session['id'],'tool/record',{'turnId':'turn','record':legacy})
    build_packet(store,brief['id'],store.root/'review');packet=store.root/'review/packet'
    tools=json.loads((packet/'history/tools.json').read_text())
    saved=json.loads((packet/tools[0]['file']).read_text())
    assert 'fake_legacy' not in json.dumps(saved) and saved['record']['redacted'] is True
    assert 'Revenue: 12' in saved['record']['output']
    assert saved['journal_record_hash']=='synthetic-old-hash'
    assert saved['record']['record_hash']!='synthetic-old-hash'
    original=json.loads(store.rows("SELECT data FROM chat_events WHERE kind='tool/record'")[0]['data'])
    assert original['record']==legacy


def test_token_fields_and_provider_credentials_are_not_persisted(tmp_path):
    store = Store(tmp_path); chat = ChatStore(store)
    session = chat.create('Synthetic credentials', {}, store.root)
    secrets = ['ghp_' + 'a' * 36, 'github_pat_' + 'b' * 48, 'pypi-' + 'c' * 32,
               'glpat-' + 'd' * 24, 'xoxb-' + 'e' * 24]
    journal_tool(chat, session['id'], 'turn', 'credentials', 'bash',
                 {'token': 'synthetic_token', 'nested': [{'idToken': 'synthetic_id_token',
                   'GITHUB_TOKEN': 'synthetic_github_token'}], 'session_id': 'session-visible',
                  'input_tokens': 42, 'token_count': 9},
                 ' '.join(secrets) + '\nTOKEN=synthetic_assigned_token\nAWS_SESSION_TOKEN=synthetic_aws_token\nRevenue: 12',
                 status='completed', native_session='native-visible')
    record = json.loads(store.rows("SELECT data FROM chat_events WHERE kind='tool/record'")[0]['data'])['record']
    encoded = json.dumps(record)
    for secret in secrets + ['synthetic_token', 'synthetic_id_token', 'synthetic_github_token',
                              'synthetic_assigned_token', 'synthetic_aws_token']:
        assert secret not in encoded
    assert record['input']['input_tokens'] == 42 and record['input']['token_count'] == 9
    assert record['input']['session_id'] == 'session-visible'
    assert record['native_session'] == 'native-visible' and 'Revenue: 12' in record['output']
    assert sanitize(record) == record
