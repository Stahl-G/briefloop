"""Launch tools from the same installation that constructed their command.

Agents change their working directory. A relative PYTHONPATH or another editable
checkout must not redirect a tool to a different BriefLoop version.
"""
from pathlib import Path
import sys


def command(*arguments):
    return [sys.executable, str(Path(__file__).resolve()), *map(str, arguments)]


if __name__ == '__main__':
    import runpy
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    runpy.run_module('briefloop', run_name='__main__', alter_sys=True)
