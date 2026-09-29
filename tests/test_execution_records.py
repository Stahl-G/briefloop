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
