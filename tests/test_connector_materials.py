"""Real local HTTP material acquisition; no accounts, model calls or wire mocks."""
import concurrent.futures
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from briefloop.store import Store
from briefloop.connectors import ConnectorService, ConnectorError


@unittest.skipUnless(importlib.util.find_spec('mcp'), 'Official MCP SDK required')
class MaterialTests(unittest.TestCase):
    def setUp(self):
        from briefloop.connectors.materials import ConnectorMaterials
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'workspace')
        self.service = ConnectorService(self.store.root)
        self.addCleanup(self.service.close)
        self.materials = ConnectorMaterials(self.store, self.service)
        self.directory = self.root / 'server'
        self.directory.mkdir()
        (self.directory / 'document.txt').write_text('Controlled source: eight of ten milestones completed.\n')
        fixture = Path(__file__).resolve().parents[1] / 'probes/mcp_m0/server.py'
        self.process = subprocess.Popen([sys.executable, str(fixture), '--transport', 'http', '--directory', str(self.directory)],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self.stop_server)
        deadline = time.monotonic() + 5
        while not (self.directory / 'ready.json').exists():
            if self.process.poll() is not None or time.monotonic() > deadline:
                self.fail('Real HTTP fixture failed to start')
            time.sleep(.02)
        self.config = {'name': 'Controlled material', 'transport': 'http',
                       'url': json.loads((self.directory / 'ready.json').read_text())['url'],
                       'timeout_seconds': 2, 'max_response_bytes': 65536}
        self.connector = self.service.save(self.config)['id']
        self.assertEqual(self.service.enable(self.connector)['state'], 'connected')
        self.run = self.store.create_run({'title': 'Controlled report', 'objective': 'Use the selected material.', 'allow_web': True}, [])

    def stop_server(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)

    def freeze(self, calls=2, tools=None):
        return self.materials.freeze(self.run['id'], [{'connector_id': self.connector,
            'resources': ['m0://document'], 'tools': tools or []}], max_calls=calls, max_total_bytes=131072)

    def read(self, grant, request='read-1'):
        return self.materials.read(grant['id'], self.run['id'], self.connector, 'm0://document', request_id=request)

    def test_real_source_and_original_survive_disconnect_and_bind_review_packet(self):
        from briefloop.media import source_files
        from briefloop.review import build_packet
        grant = self.freeze()
        result = self.read(grant)
        self.assertEqual(result['status'], 'admitted')
        source = self.store.one('sources', result['source_id'])
        self.assertEqual(self.store.source_text(source['id']), (self.directory / 'document.txt').read_text())
        self.assertIn(source['id'], self.store.source_ids(self.run['id']))
        _, provenance, original = source_files(self.store, source['id'])
        self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), provenance['raw_sha256'])
        envelope = json.loads(original.read_bytes())
        self.assertEqual(envelope['payload']['contents'][0]['text'], self.store.source_text(source['id']))
        self.assertEqual(envelope['origin']['grant_snapshot_hash'], grant['data']['snapshot_hash'])
        self.assertEqual(envelope['origin']['run_id'], self.run['id'])
        self.assertEqual(provenance['representation'], 'sdk_decoded')
        self.assertNotIn('credential', json.dumps(grant))
        self.service.disable(self.connector)
        self.stop_server()
        self.assertEqual(self.read(grant)['source_id'], source['id'])  # Reuse, never replay.
        brief = self.store.publish(self.run['id'], {'title': 'Controlled report', 'markdown': 'Eight of ten milestones completed.',
                                                  'citations': [{'source_id': source['id'], 'locator': 'line 1'}]})
        packet_folder = self.store.root / 'review'
        _, files = build_packet(self.store, brief['id'], packet_folder)
        self.assertIn('sources/' + source['id'] + '.json', files)
        self.assertEqual((packet_folder / 'packet/sources' / (source['id'] + '.json')).read_bytes(), original.read_bytes())
        original.write_text('{}')
        with self.assertRaises(ValueError):
            source_files(self.store, source['id'])

    def test_allowlist_budget_and_idempotency_precede_network(self):
        grant = self.freeze(calls=1)
        with self.assertRaises(ConnectorError):
            self.materials.read(grant['id'], self.run['id'], self.connector, 'file:///etc/passwd', request_id='denied')
        with self.assertRaises(ConnectorError):
            self.materials.read(grant['id'], 'wrong-run', self.connector, 'm0://document', request_id='other-run')
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: self.read(grant), range(2)))
        admitted = next(row for row in results if row['status'] == 'admitted')
        self.assertEqual(self.read(grant)['source_id'], admitted['source_id'])
        events = [json.loads(line) for line in (self.directory / 'server.jsonl').read_text().splitlines()]
        self.assertEqual(sum(e['event'] == 'read_document' for e in events), 1)
        with self.assertRaises(ConnectorError):
            self.read(grant, 'read-2')

    def test_config_change_and_revoke_deny_new_calls(self):
        grant = self.freeze()
        self.service.save({**self.config, 'name': 'Changed'}, connector_id=self.connector)
        self.service.enable(self.connector)
        result = self.read(grant)
        self.assertEqual(result['status'], 'not_admitted')
        self.assertFalse(self.store.source_ids(self.run['id']))
        fresh = self.freeze()
        self.materials.revoke(fresh['id'], self.run['id'])
        with self.assertRaises(ConnectorError):
            self.read(fresh)

    def test_late_real_receipt_cannot_be_admitted_after_revoke_or_disable(self):
        # Pause *after a real SDK response*, then race cancellation against local
        # admission; the hook does not fabricate or replace the returned payload.
        import threading
        from unittest.mock import patch
        real_read = self.service.read
        for action in ('revoke', 'disable'):
            with self.subTest(action=action):
                self.service.enable(self.connector)
                grant = self.freeze()
                received, release = threading.Event(), threading.Event()
                def paused_read(*args, **kwargs):
                    result = real_read(*args, **kwargs)
                    received.set()
                    if not release.wait(5): raise RuntimeError('test admission barrier expired')
                    return result
                with patch.object(self.service, 'read', side_effect=paused_read), concurrent.futures.ThreadPoolExecutor(1) as pool:
                    future = pool.submit(self.read, grant)
                    self.assertTrue(received.wait(5))
                    if action == 'revoke': self.materials.revoke(grant['id'], self.run['id'])
                    else:
                        self.service.disable(self.connector)
                        self.service.enable(self.connector)  # Same config cannot revive a late receipt.
                    release.set()
                    result = future.result(timeout=5)
                self.assertEqual(result['status'], 'not_admitted')
                self.assertTrue(result['receipt_id'])
                self.assertFalse(self.store.rows('SELECT id FROM sources'))

    def test_revoke_or_config_change_during_startup_prevents_material_send(self):
        import threading
        from unittest.mock import patch
        real_start = self.service._start
        for action in ('revoke', 'change_config'):
            with self.subTest(action=action):
                grant = self.freeze()
                entering, release = threading.Event(), threading.Event()
                def paused_start(record, scope, generation):
                    if scope == self.materials.scope(grant['id']):
                        entering.set()
                        if not release.wait(5): raise RuntimeError('test startup barrier expired')
                    return real_start(record, scope, generation)
                with patch.object(self.service, '_start', side_effect=paused_start), concurrent.futures.ThreadPoolExecutor(1) as pool:
                    future = pool.submit(self.read, grant)
                    self.assertTrue(entering.wait(5))
                    if action == 'revoke': self.materials.revoke(grant['id'], self.run['id'])
                    else:
                        self.service.save({**self.config, 'name': 'New revision'}, connector_id=self.connector)
                        self.service.enable(self.connector)
                    release.set()
                    self.assertEqual(future.result(timeout=5)['status'], 'not_admitted')
        events = [json.loads(line) for line in (self.directory / 'server.jsonl').read_text().splitlines()]
        self.assertFalse(any(row['event'] == 'read_document' for row in events))

    def test_error_and_timeout_keep_receipts_without_sources_or_replay(self):
        grant = self.freeze(tools=['explicit_failure', 'slow_read'])
        error = self.materials.call(grant['id'], self.run['id'], self.connector, 'explicit_failure', {}, request_id='error')
        self.assertEqual(error['status'], 'not_admitted')
        unknown = self.materials.call(grant['id'], self.run['id'], self.connector, 'slow_read', {'token': 'timeout'}, request_id='timeout')
        self.assertEqual(unknown['status'], 'not_admitted')
        again = self.materials.call(grant['id'], self.run['id'], self.connector, 'slow_read', {'token': 'timeout'}, request_id='timeout')
        self.assertEqual(again['receipt_id'], unknown['receipt_id'])
        self.assertFalse(self.store.rows('SELECT id FROM sources'))

    def test_byte_budget_and_rollback_leave_no_visible_source(self):
        from unittest.mock import patch
        grant = self.materials.freeze(self.run['id'], [{'connector_id': self.connector, 'resources': ['m0://document']}],
                                      max_calls=3, max_total_bytes=65536)
        real_add = self.store.add_source
        def rollback(*args, **kwargs):
            real_add(*args, **kwargs)
            raise ValueError('Controlled admission rollback')
        with patch.object(self.store, 'add_source', side_effect=rollback):
            result = self.read(grant)
        self.assertEqual(result['status'], 'not_admitted')
        self.assertFalse(self.store.rows('SELECT id FROM sources'))
        self.assertFalse(list((self.store.root / 'sources').glob('src_*')))
        self.assertTrue(self.store.rows('SELECT receipt FROM connector_operations')[0]['receipt'])
        with self.assertRaises(ConnectorError): self.read(grant, 'new-request')


    def generation_server(self):
        import http.client
        import threading
        from briefloop.server import make_server, _close_service
        server = make_server(self.store.root, port=0, paused=True)
        server.store.update_settings({'model': 'synthetic', 'model_selection_required': False,
                                      'fact_checker': False, 'auto_revision': False})
        server.connectors.enable(self.connector)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def cleanup():
            server.shutdown()
            thread.join(timeout=5)
            _close_service(server)
        self.addCleanup(cleanup)
        base = f'http://127.0.0.1:{server.server_port}'
        def request(body=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
            headers = {'Content-Type': 'application/json', 'Origin': base}
            if body is not None:
                headers['X-BriefLoop-Token'] = token
            connection.request('POST' if body is not None else 'GET', '/api/generate' if body is not None else '/api/session',
                               json.dumps(body) if body is not None else None, headers)
            response = connection.getresponse()
            result = response.status, json.loads(response.read())
            connection.close()
            return result
        token = request()[1]['token']
        return server, request

    def selection(self):
        return {'selections': [{'connector_id': self.connector, 'resources': ['m0://document'], 'tools': []}],
                'max_calls': 1, 'max_total_bytes': 65536}

    def test_default_http_connector_report_reads_and_cites_selected_material(self):
        import threading
        from briefloop import research_plan
        from briefloop.scout_coverage import declare
        server, request = self.generation_server()
        marker = 'SELECTED-CONNECTOR-MATERIAL: eight milestones completed.'
        (self.directory / 'document.txt').write_text(marker + '\n')
        status, job = request({'requirements': {'title': 'Connector report', 'objective': 'Read selected material',
                                               'allow_web': False, 'fact_check': False},
                               'source_ids': [], 'connector_selection': self.selection()})
        self.assertEqual(status, 200, job)
        run_id = json.loads(job['payload'])['run_id']
        self.assertEqual(json.loads(server.store.one('runs', run_id)['requirements'])['completion_mode'], 'standard')
        connector_id = self.connector
        class Author:
            cancelled = threading.Event()
            def execute(author, current_job, prompt, folder, on_tick, **kwargs):
                source_index = json.loads((folder / 'input.json').read_text())['sources']
                assert len(source_index) == 1, 'The author must receive the selected resource in its ordinary source index'
                source_id = source_index[0]['source_id']
                original = Path(source_index[0]['absolute_path']).read_text()
                # The author cannot use the old loopback client; material must already
                # be available before this offline turn and any Scout/Analyst handoff.
                assert marker in original
                resource_results = json.loads((folder / 'input.json').read_text())['connector_resources']
                assert resource_results[0]['status'] == 'admitted' and resource_results[0]['source_id'] == source_id
                assert not list((server.store.root / '.connector-access').glob('*.json'))
                declare(server.store, run_id, [])
                research_plan.finish_round(server.store, run_id)
                schema = json.loads((folder / 'reader_contract.schema.json').read_text())
                spec = json.loads((folder / 'input.json').read_text())['deliverable_spec']
                contract = {'source_fingerprint': schema['properties']['source_fingerprint']['const'],
                            'clauses': [{'requirement_id': row['requirement_id'], 'kind': 'reader_content',
                                         'source_quote': row['text'], 'instruction': row['text']} for row in spec['requirement_items']]}
                plan = {'reader_contract': contract, 'scout_tasks': []}
                from briefloop.analyst import packet
                writer = packet(server.store, run_id, folder / 'writer', plan=plan, research={'sources': [], 'gaps': []})
                writer_sources = json.loads((writer['root'] / 'source-index.json').read_text())['sources']
                assert writer_sources[0]['source_id'] == source_id
                assert (writer['root'] / writer_sources[0]['text_file']).read_text() == original
                (folder / 'plan.json').write_text(json.dumps(plan))
                (folder / 'draft.json').write_text(json.dumps({'title': 'Connector report',
                    'markdown': original + f'\n[@{source_id}]', 'citations': [{'source_id': source_id, 'locator': 'line 1'}]}))
                on_tick()
                return {'status': 'complete', 'returncode': 0}
        author = Author()
        server.worker.runtime = author
        server.store.update_job(job['id'], 'running')
        result = server.worker.generate(server.store.one('jobs', job['id']), score=False)
        brief = server.store.one('briefs', result['version_id'])
        receipts = server.store.rows("SELECT status,source_id FROM connector_operations WHERE status='admitted'")
        self.assertEqual(len(receipts), 1)
        source_id = receipts[0]['source_id']
        self.assertIn(marker, brief['markdown'])
        self.assertIn(f'[@{source_id}]', brief['markdown'])
        self.assertEqual(server.store.source_text(source_id), marker + '\n')
        self.assertIn(source_id, server.store.source_ids(run_id))
        self.assertFalse(list((server.store.root / '.connector-access').glob('*.json')))
        replay = server.connector_tasks.prepare_resources(job['id'])
        self.assertEqual(replay[0]['source_id'], source_id)
        events = [json.loads(line) for line in (self.directory / 'server.jsonl').read_text().splitlines()]
        self.assertEqual(sum(row['event'] == 'read_document' for row in events), 1)

    def test_unsupported_connector_requests_fail_before_admitting_a_report(self):
        from types import SimpleNamespace
        from briefloop.chat_tools import workspace_action
        from briefloop.connectors.tasks import TaskMaterials
        from briefloop.native_orchestrator import action
        from briefloop.native_roles import ToolError
        server, request = self.generation_server()
        req = {'title': 'Connector report', 'objective': 'Read selected material', 'allow_web': True, 'fact_check': False}
        before = len(server.store.rows('SELECT id FROM runs')), len(server.store.rows('SELECT id FROM jobs'))
        status, _ = request({'requirements': {**req, 'completion_mode': 'direct'},
                             'source_ids': [], 'connector_selection': self.selection()})
        self.assertEqual(status, 400)
        tool_selection = {'selections': [{'connector_id': self.connector, 'resources': [], 'tools': ['inspect_document']}],
                          'max_calls': 1, 'max_total_bytes': 65536}
        status, _ = request({'requirements': {**req, 'allow_web': False}, 'source_ids': [], 'connector_selection': tool_selection})
        self.assertEqual(status, 400)
        events = [json.loads(line) for line in (self.directory / 'server.jsonl').read_text().splitlines()]
        self.assertFalse(any(row['event'] in ('read_document', 'inspect_document') for row in events))
        with self.assertRaises(ValueError):
            server.store.create_run({**req, 'completion_mode': 'direct'}, [], connector_selection_validated=True)
        tasks = TaskMaterials(server.store, server.connectors)
        with self.assertRaises(ValueError):
            tasks.enqueue({**req, 'completion_mode': 'direct'}, [], self.selection())
        unsupported = {'action': 'generate', 'requirements': req, 'source_ids': [], 'connector_selection': self.selection()}
        with self.assertRaises(ValueError):
            workspace_action(server.store, unsupported)
        config = {'native_role': 'chat', 'session_id': 'controlled',
                  '_harness': SimpleNamespace(cancel_requested=lambda _: False)}
        with self.assertRaises(ToolError):
            action(server.store, config, {'request': unsupported})
        self.assertEqual(before, (len(server.store.rows('SELECT id FROM runs')), len(server.store.rows('SELECT id FROM jobs'))))
        server.store.update_settings({'agent_backend': 'briefloop-native', 'model': 'fixture/model'})
        native_job = tasks.enqueue({**req, 'allow_web': False}, [], tool_selection)
        self.assertFalse(server.store.rows('SELECT id FROM connector_operations'))
        from briefloop.native_orchestrator import connector
        broker = {'job_id': native_job['id'], '_harness': SimpleNamespace(connector_tasks=tasks)}
        response = connector(server.store, broker, {'request': {'action': 'call', 'connector_id': self.connector,
                                                              'name': 'inspect_document', 'arguments': {}, 'request_id': 'native-read'}})
        native_receipt = json.loads(response['content'][0]['text'])
        self.assertEqual(native_receipt['status'], 'admitted')
        server.store.update_settings({'agent_backend': 'codex', 'model': 'synthetic'})
        source = server.store.add_source('Imported source', 'The connector material was already imported.')
        accepted = workspace_action(server.store, {'action': 'generate', 'requirements': {**req, 'allow_web': False},
                                                   'source_ids': [source['id']]})
        saved = server.store.one('runs', accepted['run_id'])
        self.assertEqual(json.loads(saved['requirements'])['completion_mode'], 'direct')
        self.assertEqual(server.store.source_ids(saved['id']), [source['id']])
        with self.assertRaises(ConnectorError):
            tasks.bind(accepted['job_id'], self.selection()['selections'], max_calls=1, max_total_bytes=65536)
        legacy = tasks.enqueue({**req, 'completion_mode': 'standard'}, [], self.selection())
        legacy_payload = json.loads(legacy['payload'])
        legacy_run = server.store.one('runs', legacy_payload['run_id'])
        legacy_req = json.loads(legacy_run['requirements'])
        legacy_req['completion_mode'] = 'direct'
        legacy_payload.pop('connector_materials')
        # Persist the old defective direct binding shape, not a new authorized request.
        with server.store.tx() as connection:
            connection.execute('UPDATE runs SET requirements=? WHERE id=?', (json.dumps(legacy_req), legacy_run['id']))
            connection.execute('UPDATE jobs SET payload=? WHERE id=?', (json.dumps(legacy_payload), legacy['id']))
        class NeverExecute:
            def execute(self, *args, **kwargs):
                raise AssertionError('An unsupported persisted binding must stop before an author turn')
        server.worker.runtime = NeverExecute()
        with self.assertRaises(ValueError):
            server.worker.generate(server.store.one('jobs', legacy['id']), score=False)


class ComposableSourceTests(unittest.TestCase):
    def test_caller_owns_commit_rollback_and_existing_files(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            existing = store.add_source('Existing', 'Keep this text.')
            with self.assertRaises(ValueError):
                with store.tx() as connection:
                    store.add_source('Existing', 'Keep this text.', source_id=existing['id'], connection=connection)
                    store.add_source('New', 'Orphan until caller cleanup.', source_id='src_composable', connection=connection)
                    self.assertFalse(store.rows("SELECT id FROM sources WHERE id='src_composable'"))
                    raise ValueError('Rollback')
            self.assertFalse(store.rows("SELECT id FROM sources WHERE id='src_composable'"))
            self.assertEqual(store.source_text(existing['id']), 'Keep this text.')
            self.assertTrue((store.root / 'sources/src_composable.txt').exists())
            with store.tx() as connection:
                admitted = store.add_source('Committed', 'Owned transaction.', connection=connection)
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM sources').fetchone()[0], 2)
            self.assertEqual(store.source_text(admitted['id']), 'Owned transaction.')


if __name__ == '__main__': unittest.main()
