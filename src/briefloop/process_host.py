"""Windows bridge child owner; stdin/stdout stay the host's protocol pipes.

The bridge may terminate this helper for one execution. Windows then closes its
Job handle and reaps only that execution's tree, including surviving descendants.
"""
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from briefloop.platform_support import OwnedProcess


def main():
    process = OwnedProcess(sys.argv[1:], stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
    try:
        return process.wait()
    finally:
        process.close_tree()


if __name__ == '__main__':
    raise SystemExit(main())
