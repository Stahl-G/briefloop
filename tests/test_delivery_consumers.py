"""Guard against produced-but-never-consumed check signals.

The number_bindings defect class: a deterministic check produces a signal,
nothing anywhere reads it, and delivery stays green.  This test applies the
same audit that found it — every key the delivery checks return must be
referenced somewhere in the Python tree outside its producer.

Source-grep is a heuristic: generic names (``total``, ``checked``) can pass
on unrelated uses.  The value is the friction — adding a new key forces the
author to either wire a consumer or extend the allowlist below with a
justification, and distinctive names (``figure_error``, ``body_quantity_count``,
``not_checked``) cannot slip through silently.
"""
import ast
from pathlib import Path

import briefloop.delivery_checks as dc

SRC = Path(dc.__file__).resolve().parent

# Keys exempt with the consumer that justifies them.  Empty is the goal;
# entries must name a real consumer, not "probably fine".
ALLOWLIST = {
    'version_id': 'caller identity for snapshot persistence (review.py build_packet)',
    'figure_markers': 'figure id list consumed via validate_figures/markdown markers '
                      '(figure_support.py re-derives from the same regex; see check_export)',
}


def _keys_of_return(func_node):
    for node in ast.walk(func_node):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            yield from _dict_keys(node.value)


def _dict_keys(dict_node):
    for key in dict_node.keys:
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            yield key.value
    for value in dict_node.values:
        if isinstance(value, ast.Dict):
            yield from _dict_keys(value)


def test_every_delivery_check_key_has_a_consumer():
    module = ast.parse(Path(dc.__file__).read_text(encoding='utf-8'))
    producers = {'brief_checks', 'check_export', 'check_layout', 'check_numbers'}
    keys = set()
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name in producers:
            keys.update(_keys_of_return(node))

    consumers = ''
    for path in sorted(SRC.glob('*.py')):
        if path.name != 'delivery_checks.py':
            consumers += path.read_text(encoding='utf-8')

    unconsumed = sorted(k for k in keys - set(ALLOWLIST) if k not in consumers)
    assert not unconsumed, (
        'brief_checks 系列产出了无人消费的键（生产者：delivery_checks.py）：'
        + ', '.join(unconsumed)
        + '。要么接上消费方（release.py / runtime.py 提示词 / audit_bundle.py / server.py），'
          '要么在 ALLOWLIST 里写明真实消费者。这是 number_bindings 缺陷类的守护测试：'
          '有信号、无消费者 = 静默失活。')
