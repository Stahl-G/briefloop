"""BriefLoop-owned native engine: the pi SDK embedded in a long-lived process.

The engine speaks the same NDJSON stdio protocol as RuntimeBridge, so this
client swaps the bundled script. The runner chooses each role and its tools.
For the independent Reviewer, The reviewer's whole world is the generated packet —
packet_list/packet_read resolve inside it; there are no built-in tools, no
user extensions, no context-file discovery. The bundled compaction hook adds
no tools or permissions. Confinement is our own tool proxy, not
pi's read tool (which accepts absolute paths) and not prompt wording.
"""
from .runtime_bridge import RuntimeBridge


class NativeEngine(RuntimeBridge):
    SCRIPT = 'native-engine.mjs'
    NODE_MIN = '22.19'


def discovery():
    from .host_bins import find
    from .backends import BACKEND_LABELS
    from . import __version__
    import os
    node = find(os.environ.get('BRIEFLOOP_NODE') or 'node')
    return {'id': 'briefloop-native', 'name': BACKEND_LABELS['briefloop-native'], 'installed': True,
            'available': bool(node), 'integrated': True, 'version': __version__, 'path': '随 BriefLoop 安装',
            'protocol': 'BriefLoop tools',
            'capabilities': {'chat': True, 'cancel': True, 'resume': True, 'steer': False,
                             'restricted_reviewer': True, 'permission_modes': ['workspace-write', 'read-only']},
            'diagnostic': '需要 Node.js 22.19+ 和已配置的模型密钥；实际可用性可点击测试。' if node else '未找到 Node.js 22.19+，请先准备运行环境。'}
