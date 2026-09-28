"""Small, exact source windows for inspecting evidence, never support verdicts."""
import re

from .writer_assembly import locate_excerpt


def _table_row(line):
    return line.count('|') >= 2 or '\t' in line


def _table_separator(line):
    cells = line.strip().strip('|').split('|')
    return len(cells) > 1 and all(re.fullmatch(r'\s*:?-{3,}:?\s*', cell) for cell in cells)


def located_context(text, excerpt, locator=''):
    """Resolve a unique excerpt and retain neighbors, headings and table headers.

    Disjoint windows keep their own source line ranges, separated by an explicit
    ellipsis. The original excerpt stays untouched at the call site. This does
    not infer the claim's subject/period or decide that the evidence supports it.
    """
    resolved = locate_excerpt(text, excerpt, locator)
    match = re.fullmatch(r'line (\d+)(?:-(\d+))?', resolved)
    lo, hi = int(match[1]) - 1, int(match[2] or match[1]) - 1
    # locate_excerpt counts LF only; PDF form feeds are not extra source lines.
    lines = text.split('\n')
    selected = set(range(max(0, lo - 2), min(len(lines), hi + 3)))

    # Keep the active Markdown heading hierarchy, including a distant section
    # title above a long table. Do not duplicate everything up to the excerpt.
    headings = []
    for index, line in enumerate(lines[:lo]):
        heading = re.match(r'^\s{0,3}(#{1,6})\s+\S', line)
        if heading:
            level = len(heading[1])
            headings = [(depth, row) for depth, row in headings if depth < level]
            headings.append((level, index))
    selected.update(index for _, index in headings)

    # A neighboring row displayed with the excerpt also needs its table header.
    for index in sorted(selected):
        if not _table_row(lines[index]):
            continue
        start = index
        while start > 0 and _table_row(lines[start - 1]):
            start -= 1
        # Markdown tables identify the header with a separator. TSV extracts
        # generally have no separator; keep their first row without labelling
        # it as a verified semantic header.
        if start + 1 < len(lines) and _table_separator(lines[start + 1]):
            selected.update(range(max(0, start - 2), start + 2))
        elif '\t' in lines[index]:
            selected.update(range(max(0, start - 2), start + 1))

    ranges = []
    for index in sorted(selected):
        if ranges and index == ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], index)
        else:
            ranges.append((index, index))
    return {
        'locator': resolved,
        'source_context': '\n\n[…]\n\n'.join('\n'.join(lines[a:b + 1]) for a, b in ranges),
        'context_locator': '; '.join(f'line {a + 1}' if a == b else f'line {a + 1}-{b + 1}' for a, b in ranges),
    }
