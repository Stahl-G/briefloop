"""Task-scoped tools use a real local HTTP MCP server, then offline review."""
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
import test_connector_materials as fixtures
from briefloop.connectors import ConnectorError
from briefloop.connectors.tasks import TaskMaterials


@unittest.skipUnless(importlib.util.find_spec('mcp'), 'Official MCP SDK required')
class TaskMaterialTests(unittest.TestCase):
    setUp = fixtures.MaterialTests.setUp
    stop_server = fixtures.MaterialTests.stop_server

    def prepare(self):
        self.tasks = TaskMaterials(self.store, self.service)
        self.job = self.store.enqueue('generate', {'run_id': self.run['id']})
        self.tasks.bind(self.job['id'], [{'connector_id': self.connector, 'resources': ['m0://document']}],
                        max_calls=1, max_total_bytes=65536)
        self.token = self.tasks.access(self.job['id'])['access_token']
        self.request = {'action': 'read', 'connector_id': self.connector, 'uri': 'm0://document', 'request_id': 'first'}

    def test_bound_material_becomes_offline_review_snapshot(self):
        from briefloop.review import build_packet
        from briefloop.media import source_files
        self.prepare()
        with self.store.tx() as c:
            c.execute("UPDATE jobs SET status='running' WHERE id=?", (self.job['id'],))
        result = self.tasks.dispatch(self.token, self.request)
        self.assertEqual(result['status'], 'admitted')
        sid = result['source_id']
        _, metadata, original = source_files(self.store, sid)
        self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), metadata['raw_sha256'])
        self.service.disable(self.connector)
        self.stop_server()
        brief = self.store.publish(self.run['id'], {'title': 'Selected material', 'markdown': 'Eight of ten milestones completed.',
                                   'citations': [{'source_id': sid, 'locator': 'line 1'}]})
        packet, files = build_packet(self.store, brief['id'], self.root / 'review')
        self.assertEqual((self.root / 'review/packet/sources' / (sid + '.json')).read_bytes(), original.read_bytes())
        self.assertNotIn(self.token, json.dumps(packet))
        self.assertIn('sources/' + sid + '.json', files)
        with self.store.tx() as c:
            c.execute("UPDATE jobs SET status='complete' WHERE id=?", (self.job['id'],))
        with self.assertRaises(ConnectorError): self.tasks.dispatch(self.token, self.request)

    def test_no_identity_override_reauthorization_or_reviewer_access(self):
        self.prepare()
        for request in ({**self.request, 'run_id': self.run['id']}, {**self.request, 'max_calls': 100}, {'action': 'freeze'}):
            with self.assertRaises(ConnectorError): self.tasks.dispatch(self.token, request)
        with self.assertRaises(ConnectorError):
            self.tasks.bind(self.job['id'], [{'connector_id': self.connector, 'resources': ['m0://document']}], max_calls=50, max_total_bytes=65536)
        reviewer = self.store.enqueue('assess', {'run_id': self.run['id']})
        with self.assertRaises(ConnectorError): self.tasks.access(reviewer['id'])
        with self.assertRaises(ConnectorError): TaskMaterials(self.store, self.service).dispatch(self.token, self.request)
        self.tasks.revoke(self.job['id'])
        with self.assertRaises(ConnectorError): self.tasks.dispatch(self.token, self.request)
        events = [json.loads(line) for line in (self.directory / 'server.jsonl').read_text().splitlines()]
        self.assertFalse(any(event['event'] == 'read_document' for event in events))

    def test_started_job_cannot_gain_new_grant(self):
        tasks = TaskMaterials(self.store, self.service)
        job = self.store.enqueue('generate', {'run_id': self.run['id']})
        original_freeze = tasks.materials.freeze
        def start_during_freeze(*args, **kwargs):
            grant = original_freeze(*args, **kwargs)
            with self.store.tx() as c: c.execute("UPDATE jobs SET status='running' WHERE id=?", (job['id'],))
            return grant
        tasks.materials.freeze = start_during_freeze
        with self.assertRaises(ConnectorError):
            tasks.bind(job['id'], [{'connector_id': self.connector, 'resources': ['m0://document']}], max_calls=1, max_total_bytes=65536)
        self.assertFalse(self.store.rows('SELECT * FROM connector_task_bindings'))
        self.assertEqual(self.store.rows('SELECT status FROM connector_grants')[0]['status'], 'revoked')

    def test_actual_application_routes_keep_host_authorization_separate(self):
        import http.client
        import threading
        from briefloop.server import make_server
        server = make_server(self.root / 'application', port=0, paused=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(path, body=None, headers=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
            connection.request('POST' if body is not None else 'GET', path, json.dumps(body) if body is not None else None, headers or {})
            response = connection.getresponse()
            result = response.status, json.loads(response.read())
            connection.close()
            return result
        try:
            connector = server.connectors.save(self.config)['id']
            server.connectors.enable(connector)
            ui = request('/api/session')[1]['token']
            host = {'X-BriefLoop-Token': ui}
            selection = {'selections': [{'connector_id': connector, 'resources': ['m0://document']}], 'max_calls': 1, 'max_total_bytes': 65536}
            generated = request('/api/generate', {'requirements': {'title':'Bound report','objective':'Read selected material','allow_web':False}, 'source_ids':[], 'connector_selection': selection}, host)
            self.assertEqual(generated[0], 200, generated)
            job = generated[1]
            run_id = json.loads(job['payload'])['run_id']
            self.assertFalse(json.loads(server.store.one('runs',run_id)['requirements'])['allow_web'])
            self.assertTrue(server.connector_tasks.has_binding(job['id']))
            body = {'job_id': job['id'], **selection}
            self.assertEqual(request('/api/connectors/task-bind', body)[0], 403)
            access = request('/api/connectors/task-access', {'job_id': job['id']}, host)[1]['access_token']
            tool = {'action': 'read', 'connector_id': connector, 'uri': 'm0://document', 'request_id': 'http-1'}
            from briefloop.connectors.runtime_tools import generation_access
            import subprocess
            import shlex
            with generation_access(server.worker, job) as instructions:
                self.assertNotIn(access, instructions)
                command = instructions.split('再执行：',1)[1].split('\n',1)[0]
                request_path = self.root / 'request.json'
                request_path.write_text(json.dumps(tool))
                args = shlex.split(command)
                args[-1] = str(request_path)
                acquired = subprocess.run(args, capture_output=True, text=True, check=True)
                self.assertEqual(json.loads(acquired.stdout)['status'], 'admitted')
                access_path = Path(args[args.index('--access-file')+1])
                self.assertEqual(access_path.stat().st_mode & 0o777, 0o600)
            self.assertFalse(access_path.exists())
            self.assertEqual(request('/api/connectors/task-bind', body, {'Authorization': 'Bearer ' + access})[0], 403)
            server.worker.stop_job(job['id'])
            self.assertEqual(server.connector_tasks.status(job['id'])['grant']['status'], 'revoked')
            self.assertNotEqual(request('/api/connectors/task-tool', tool, {'Authorization': 'Bearer ' + access})[0], 200)
        finally:
            server.shutdown()
            thread.join()
            server.harness.close()
            server.opencode_harness.close()
            server.runtime_bridge.close()
            server.server_close()
            server.workspace_lock.close()
