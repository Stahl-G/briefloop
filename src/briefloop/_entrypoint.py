"""Absolute agent-tool entry point, independent of the agent's working directory."""
from pathlib import Path
import runpy
import sys


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ('briefloop', 'wikiskill'):
        raise SystemExit('Expected briefloop or wikiskill')
    module = sys.argv.pop(1)
    # Use the same package tree that produced the task, even from an unrelated
    # workspace or a checkout served through PYTHONPATH rather than pip install.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    runpy.run_module(module, run_name='__main__')


if __name__ == '__main__':
    main()
