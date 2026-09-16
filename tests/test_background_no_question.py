"""Background runs on every backend share one no-interactive-waiting contract (#724).

Codex answers approval prompts non-interactively by itself, opencode denies the
question tool via permission rules, and bridge hosts now fail fast on permission
requests. The prompt-level guard must therefore reach every backend, not only
opencode.
"""
import pytest

from briefloop.chat_tools import chat_instructions
from briefloop.learning import comparison_prompt
from briefloop.runtime import COMMON, COMMON_OPENCODE, generation_prompt
from briefloop.store import Store

GUARD='本轮没有任何用户在旁可问'


@pytest.mark.parametrize('backend', ['codex', 'claude', 'codebuddy'])
def test_generation_prompt_carries_the_guard_beyond_opencode(tmp_path, backend):
    store=Store(tmp_path/'workspace')
    run=store.create_run({'title':'后台研究','objective':'整理材料','allow_web':True},[])
    job=store.enqueue('generate',{'run_id':run['id']})
    folder=store.root/'jobs'/job['id'];folder.mkdir()
    prompt=generation_prompt(store,run,folder,backend=backend)
    assert GUARD in prompt


@pytest.mark.parametrize('backend', ['codex', 'opencode', 'claude'])
def test_evaluator_prompts_keep_the_guard_for_every_backend(tmp_path, backend):
    store=Store(tmp_path/'workspace')
    folder=store.root/'learning'/'case-1';folder.mkdir(parents=True)
    (folder/'input.json').write_text('{}',encoding='utf-8')
    assert GUARD in comparison_prompt(store,folder,backend=backend)
    assert GUARD in COMMON and GUARD in COMMON_OPENCODE


@pytest.mark.parametrize('backend', ['claude', 'codebuddy'])
def test_internal_bridge_contract_forbids_waiting_on_the_host(tmp_path, backend):
    store=Store(tmp_path/'workspace')
    text=chat_instructions(store,{'model':'host-model','backend':backend},internal=True)
    assert GUARD in text and '不要发起并等待宿主授权' in text
    interactive=chat_instructions(store,{'model':'host-model','backend':backend},internal=False)
    assert GUARD not in interactive
