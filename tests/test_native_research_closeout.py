"""Writing admission uses real saved rounds; the paid Analyst boundary is a spy."""
import json
import threading
from types import SimpleNamespace

import pytest

from briefloop import native_orchestrator, research_plan
from briefloop.native_roles import ToolError
from briefloop.research_budget import snapshot
from briefloop.research_handoff import gap_view
from briefloop.store import Store, dump


def prepared(tmp_path, monkeypatch, *, protocol='quality_v1', deep=False):
    store = Store(tmp_path)
    source = store.add_source('Synthetic secondary release', 'Public beta is available; original terms were not retrieved.')
    run = store.create_run({'title': 'Synthetic weekly', 'objective': 'Explain available terms.', 'allow_web': False,
        'research_tier': 'deep' if deep else 'standard',
        'research_budget': {'search_requests': 0, 'candidate_urls': 0, 'source_pages': 0}},
        [source['id']], research_protocol=protocol)
    job = store.enqueue('generate', {'run_id': run['id']})
    store.update_job(job['id'], status='running')
    packet = store.root / 'native' / 'packet'
    packet.mkdir(parents=True)
    native_orchestrator._save(packet.parent / 'plan.json', {'summary': 'Explain available terms'})
    native_orchestrator._save(packet.parent / 'research.json', {'sources': [], 'gaps': ['Original terms not retrieved']})
    harness = SimpleNamespace(cancel_requested=lambda _: False, _lock=threading.RLock())
    config = {'run_id': run['id'], 'job_id': job['id'], 'session_id': 'offline-spy',
              'packet_root': str(packet), '_harness': harness}
    calls = []
    def writer_boundary(store, config, directory, callback):
        calls.append(config['run_id'])
        directory.mkdir(parents=True, exist_ok=True)
        native_orchestrator._save(directory / 'draft.json', {'title': 'Boundary fixture', 'markdown': 'Fixture only.'})
        return {'boundary_reached': True}
    monkeypatch.setattr(native_orchestrator, '_child', writer_boundary)
    return store, run, config, calls
