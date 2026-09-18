"""BriefLoop-owned native engine: the pi SDK embedded in a long-lived process.

The engine speaks the same NDJSON stdio protocol as RuntimeBridge, so this
client only swaps the bundled script. Phase 1 exposes exactly one role: the
restricted Reviewer. The reviewer's whole world is the generated packet —
packet_list/packet_read resolve inside it; there are no built-in tools, no
extensions, no context-file discovery. Confinement is our own tool proxy, not
pi's read tool (which accepts absolute paths) and not prompt wording.
"""
from .runtime_bridge import RuntimeBridge


class NativeEngine(RuntimeBridge):
    SCRIPT = 'native-engine.mjs'
    NODE_MIN = '22.19'
