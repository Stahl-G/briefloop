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

    def prepare(self, calls=1):
        self.tasks = TaskMaterials(self.store, self.service)
        self.job = self.store.enqueue('generate', {'run_id': self.run['id']})
        self.tasks.bind(self.job['id'], [{'connector_id': self.connector, 'resources': ['m0://document']}],
                        max_calls=calls, max_total_bytes=calls * 65536)
        self.token = self.tasks.access(self.job['id'])['access_token']
        self.request = {'action': 'read', 'connector_id': self.connector, 'uri': 'm0://document', 'request_id': 'first'}


