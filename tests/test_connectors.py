"""Bounded connector settings and real protocol behavior checks."""
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('briefloop.connectors'), 'Connector backend is missing')
        from briefloop.connectors import ConnectorService
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.service = ConnectorService(self.root)
        self.addCleanup(self.service.close)

    def stdio_config(self):
        directory = self.root / 'server'
        directory.mkdir(exist_ok=True)
        (directory / 'document.txt').write_text('Actual local connector material.\n')
        fixture = Path(__file__).resolve().parents[1] / 'probes/mcp_m0/server.py'
        return {'name': 'Local material', 'transport': 'stdio', 'command': sys.executable,
                'args': [str(fixture), '--transport', 'stdio', '--directory', str(directory)],
                'cwd': str(directory), 'timeout_seconds': 3}

    def test_save_never_executes_and_secrets_stay_local(self):
        saved = self.service.save(self.stdio_config(), secrets={'env': {'MY_TEST_KEY': 'local-only-secret'}})
        self.assertFalse(saved['enabled'])
        self.assertFalse((self.root / 'server/server.jsonl').exists())
        self.assertNotIn('local-only-secret', json.dumps(self.service.list()))
        self.assertEqual(saved['env_names'], ['MY_TEST_KEY'])
        self.assertTrue(saved['has_credentials'])
        from briefloop.connectors import ConnectorService
        other = ConnectorService(self.root)
        self.addCleanup(other.close)
        self.assertEqual(other.list()[0]['id'], saved['id'])
        self.assertFalse((self.root / 'server/server.jsonl').exists())
        for path in (self.root / '.connectors').rglob('*'):
            if path.is_file():
                self.assertEqual(path.stat().st_mode & 0o077, 0)

    @unittest.skipUnless(importlib.util.find_spec('mcp'), 'MCP SDK not installed')
    def test_real_stdio_preview_enable_scope_isolation_and_disable(self):
        saved = self.service.save(self.stdio_config())
        identifier = saved['id']
        preview = self.service.test(identifier)
        self.assertTrue(preview['ok'])
        self.assertIn('inspect_document', [t['name'] for t in preview['capabilities']['tools']])
        self.assertFalse(self.service.status(identifier)['enabled'])
        self.assertEqual(self.service.enable(identifier)['state'], 'connected')
        first = self.service.call(identifier, 'inspect_document', {}, scope_id='run-a')
        again = self.service.call(identifier, 'inspect_document', {}, scope_id='run-a')
        other = self.service.call(identifier, 'inspect_document', {}, scope_id='run-b')
        a = first['payload']['structuredContent']
        self.assertEqual(a['pid'], again['payload']['structuredContent']['pid'])
        self.assertNotEqual(a['pid'], other['payload']['structuredContent']['pid'])
        read = self.service.read(identifier, 'file:///briefloop-m0-server/document.txt', scope_id='run-a')
        self.assertEqual(read['payload']['contents'][0]['text'], 'Actual local connector material.\n')
        self.assertEqual(self.service.disable(identifier)['state'], 'disabled')
        from briefloop.connectors import ConnectorError
        with self.assertRaises(ConnectorError):
            self.service.call(identifier, 'inspect_document', {}, scope_id='run-a')

    @unittest.skipUnless(importlib.util.find_spec('mcp'), 'MCP SDK not installed')
    def test_disable_cancels_inflight_and_cleans_descendants(self):
        identifier = self.service.save(self.stdio_config())['id']
        self.service.enable(identifier)
        child = self.service.call(identifier, 'spawn_owned_child', {}, scope_id='run-a')['payload']['structuredContent']
        pool = concurrent.futures.ThreadPoolExecutor(1)
        self.addCleanup(pool.shutdown)
        future = pool.submit(self.service.call, identifier, 'slow_read', {'token': 'stop'}, scope_id='run-a')
        deadline = time.monotonic() + 5
        path = self.root / 'server/server.jsonl'
        while 'slow_started' not in path.read_text():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.02)
        self.service.disable(identifier)
        result = future.result(timeout=3)
        self.assertIn(result['delivery'], ('cancelled', 'unknown'))
        with self.assertRaises(ProcessLookupError):
            os.kill(child['pid'], 0)

    def http_config(self):
        self.stdio_config()
        directory = self.root / 'server'
        fixture = Path(__file__).resolve().parents[1] / 'probes/mcp_m0/server.py'
        process = subprocess.Popen([sys.executable, str(fixture), '--transport', 'http', '--directory', str(directory)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def stop():
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        self.addCleanup(stop)
        deadline = time.monotonic() + 5
        while not (directory / 'ready.json').exists():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.02)
        return {'name': 'HTTP material', 'transport': 'http',
                'url': json.loads((directory / 'ready.json').read_text())['url'],
                'timeout_seconds': 3, 'max_response_bytes': 65536}

    @unittest.skipUnless(importlib.util.find_spec('mcp'), 'MCP SDK not installed')
    def test_real_http_settings_and_error_guard(self):
        config = self.http_config()
        identifier = self.service.save(config, secrets={'bearer_token': 'local-test-token'})['id']
        self.assertTrue(self.service.test(identifier)['ok'])
        self.assertEqual(self.service.enable(identifier)['state'], 'connected')
        result = self.service.read(identifier, 'm0://document', scope_id='run-a')
        self.assertEqual(result['payload']['contents'][0]['text'], 'Actual local connector material.\n')
        disabled = self.service.disable(identifier)
        self.assertEqual(disabled['state'], 'disabled')
        config['url'] = config['url'].rsplit('/', 1)[0] + '/deny401'
        self.service.save(config, connector_id=identifier)
        before = len((self.root / 'server/server.jsonl').read_text().splitlines())
        failure = self.service.test(identifier)
        self.assertFalse(failure['ok'])
        self.assertEqual(failure['error']['code'], 'authentication_required')
        self.assertNotIn('local-test-token', json.dumps(failure))
        es = [json.loads(line) for line in (self.root / 'server/server.jsonl').read_text().splitlines()[before:]]
        self.assertEqual([e['method'] for e in es if e['event'] == 'http_rpc'], ['server/discover'])
        self.assertTrue(self.service.delete(identifier)['deleted'])
        self.assertEqual(self.service.list(), [])

    @unittest.skipUnless(importlib.util.find_spec('mcp'), 'MCP SDK not installed')
    def test_response_limits_reject_large_real_resources(self):
        for transport in ('http', 'stdio'):
            with self.subTest(transport=transport):
                config = self.http_config() if transport == 'http' else self.stdio_config()
                config['max_response_bytes'] = 65536
                identifier = self.service.save(config)['id']
                self.assertEqual(self.service.enable(identifier)['state'], 'connected')
                (self.root / 'server/document.txt').write_text('large material ' * 10000)
                result = self.service.read(identifier, 'm0://document', scope_id='oversized')
                self.assertEqual(result['delivery'], 'unknown')
                self.assertEqual(result['error']['code'], 'response_limit')
                self.assertEqual(self.service.status(identifier)['state'], 'error')
                self.service.disable(identifier)

    @unittest.skipUnless(importlib.util.find_spec('mcp'), 'MCP SDK not installed')
    def test_disable_interrupts_connection_startup(self):
        config = self.stdio_config()
        config['args'] = ['-c', 'import time; time.sleep(30)']
        identifier = self.service.save(config)['id']
        pool = concurrent.futures.ThreadPoolExecutor(1)
        self.addCleanup(pool.shutdown)
        future = pool.submit(self.service.enable, identifier)
        deadline = time.monotonic() + 5
        while self.service.status(identifier)['state'] != 'connecting':
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)
        started = time.monotonic()
        self.service.disable(identifier)
        result = future.result(timeout=3)
        self.assertFalse(result['enabled'])
        self.assertLess(time.monotonic() - started, 3)

    def test_invalid_url_and_secret_fields_are_rejected(self):
        from briefloop.connectors import ConnectorError
        for url in ['http://example.com/mcp', 'https://name:secret@example.com/mcp', 'https://example.com/mcp?key=secret']:
            with self.assertRaises(ConnectorError):
                self.service.save({'name': 'invalid', 'transport': 'http', 'url': url})
        with self.assertRaises(ConnectorError):
            self.service.save({'name': 'invalid', 'transport': 'stdio', 'command': 'npx', 'args': ['uninstalled-server']})


if __name__ == '__main__':
    unittest.main()
