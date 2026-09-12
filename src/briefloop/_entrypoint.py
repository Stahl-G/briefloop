"""Launch tools from the same installation that constructed their command.

Agents change their working directory. A relative PYTHONPATH or another editable
checkout must not redirect a tool to a different BriefLoop version.
"""
from pathlib import Path
import runpy
import sys


def command(*arguments, module='briefloop'):
    """Return subprocess argv; shell quoting belongs to the caller."""
    if module not in ('briefloop', 'wikiskill'):
        raise ValueError('Unsupported agent tool module: ' + str(module))
    return [Path(sys.executable).as_posix(), '-X', 'utf8',
            Path(__file__).resolve().as_posix(),
            *([module] if module != 'briefloop' else []), *map(str, arguments)]


def main():
    # Older frozen Windows task packets explicitly named either package.
    # Keep accepting those commands while command('tool', ...) retains the
    # upstream API without a package selector.
    module = 'briefloop'
    if len(sys.argv) > 1 and sys.argv[1] in ('briefloop', 'wikiskill'):
        module = sys.argv.pop(1)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    runpy.run_module(module, run_name='__main__', alter_sys=True)


if __name__ == '__main__':
    main()
