"""Frozen per-role selection reaches both transports and keeps scoring separate."""
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from briefloop.models import ROLE_NAMES, Settings
from briefloop.store import Store, dump
from briefloop.runtime import Worker, stage_job
from briefloop.interactive_runtime import InteractiveRuntime
from briefloop.learning import enqueue_feedback, _role
from briefloop.learning_budget import plan as learning_plan


def test_role_model_from_another_runtime_inherits_instead_of_blocking(tmp_path):
    store=Store(tmp_path/'workspace')
    store.set_meta('settings',{**store.settings(),'agent_backend':'codex','model':'gpt-5.6-luna',
        'model_selection_required':False,'role_models':{'evaluator':{'model':'gpt-5.6-luna','reasoning_effort':'high'}}})
    store.set_meta('settings',{**store.settings(),'agent_backend':'opencode','model':'opencode-go/gpt-5.6-luna',
        'model_selection_required':False})
    # The stranded Codex id stays visible so the user can clear it...
    assert store.settings()['role_models']['evaluator']['model']=='gpt-5.6-luna'
    # ...but it must not fail the whole run with the other runtime's model rules.
    payload=json.loads(store.enqueue('generate',{})['payload'])
    assert payload['runtime']=={'model':'opencode-go/gpt-5.6-luna'}
    assert payload['role_models']['evaluator']==payload['runtime']
    assert payload['role_models']['maintainer']==payload['runtime']


