"""Fail-closed loader for frozen #757 benchmark materials, never model input.

The manifest and caches are separate from copied product workspaces. Reader
contracts, sentence labels, paraphrase judgments and chapter expectations are
used only to verify perturbations and stratify the offline results.
"""
import hashlib
import json
import sqlite3
from pathlib import Path

RULES = 'r2'


def _sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def reader_contract_hash(requirements):
    return _sha(json.dumps(requirements, ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def _json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError(f'Invalid materials file {path.name}: {exc}') from exc


def _relative(root, reference):
    if not isinstance(reference, str) or not reference or Path(reference).is_absolute():
        raise ValueError('Material references must be relative paths')
    path = (root / reference).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f'Material reference escapes manifest directory: {reference}')
    if not path.is_file():
        raise ValueError(f'Material file missing: {reference}')
    return path


def read_frozen_version(workspace, version):
    db = Path(workspace).resolve() / 'briefloop.db'
    if not db.is_file():
        raise ValueError(f'Slice database missing: {db}')
    # Read-only URI avoids modifying the original frozen workspace or creating a DB.
    with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as connection:
        row = connection.execute('SELECT markdown,run_id FROM briefs WHERE id=?', (version,)).fetchone()
        if row is None or not isinstance(row[0], str):
            raise ValueError(f'Frozen version missing: {version}')
        contract = connection.execute('SELECT requirements FROM runs WHERE id=?', (row[1],)).fetchone()
    if contract is None:
        raise ValueError(f'Reader contract missing: {version}')
    try:
        requirements = json.loads(contract[0])
    except (TypeError, ValueError) as exc:
        raise ValueError(f'Invalid reader contract: {version}') from exc
    if not isinstance(requirements, dict):
        raise ValueError(f'Invalid reader contract: {version}')
    return row[0], requirements


def validate_workspace(workspace, version, material):
    markdown, requirements = read_frozen_version(workspace, version)
    if material['markdown_sha256'] != _sha(markdown):
        raise ValueError(f"Stale Markdown material for {material['name']}")
    if material['reader_contract_sha256'] != reader_contract_hash(requirements):
        raise ValueError(f"Stale reader contract material for {material['name']}")
    return markdown


def _validate_labels(markdown, labels):
    import seed_value
    blocks, body = seed_value._body_paragraphs(markdown)
    paragraphs = labels.get('paragraphs')
    if not isinstance(paragraphs, list):
        raise ValueError('Label cache must contain paragraphs')
    seen = set()
    for paragraph in paragraphs:
        if not isinstance(paragraph, dict) or 'error' in paragraph:
            raise ValueError('Incomplete or failed sentence labels')
        block = paragraph.get('block')
        if isinstance(block, bool) or not isinstance(block, int) or block not in body or block in seen:
            raise ValueError('Label paragraph address does not match frozen Markdown')
        seen.add(block)
        actual = [seed_value._plain(s) for s in seed_value._sentences(blocks[block])]
        sentences = paragraph.get('sentences')
        if not isinstance(sentences, list) or len(sentences) != len(actual):
            raise ValueError('Label sentence count does not match frozen Markdown')
        for index, (item, text) in enumerate(zip(sentences, actual)):
            if (not isinstance(item, dict) or isinstance(item.get('i'), bool) or item.get('i') != index
                    or item.get('text') != text or item.get('label') not in ('fact', 'implication', 'unresolved')):
                raise ValueError('Label sentence address/text does not match frozen Markdown')
    expected = {b for b in body if seed_value._sentences(blocks[b])}
    if seen != expected:
        raise ValueError('Label cache omits frozen body paragraphs')


def _identity(cache, material, kind):
    for key, expected in (('slice', material['name']), ('version', material['version']), ('rules', RULES),
                          ('markdown_sha256', material['markdown_sha256']),
                          ('reader_contract_sha256', material['reader_contract_sha256'])):
        if cache.get(key) != expected:
            raise ValueError(f'{kind} {key} does not match frozen materials')


def _validate_paraphrases(markdown, labels, cache):
    import seed_value
    pairs, checks = cache.get('pairs'), cache.get('checks')
    if not isinstance(pairs, dict) or not isinstance(checks, dict) or set(pairs) != set(checks):
        raise ValueError('Paraphrases require an explicit same check for every pair')
    originals = {seed_value._plain(s) for _, _, s in seed_value.judgments(markdown, labels)}
    for original, replacement in pairs.items():
        if checks[original] != 'same':
            raise ValueError('Changed or uncertain paraphrases are not invariant materials')
        if original not in originals or not isinstance(replacement, str) or not replacement.strip():
            raise ValueError('Paraphrase does not address a frozen implication sentence')
        if original == replacement.strip():
            raise ValueError('An unchanged paraphrase is not an applicable perturbation')
    return pairs


def load_materials(manifest_path, slice_dir, slices):
    """Verify selected source slices and all their material caches before dispatch."""
    manifest_path, slice_dir = Path(manifest_path).expanduser().resolve(), Path(slice_dir).expanduser().resolve()
    manifest = _json(manifest_path)
    if manifest.get('schema_version') != 1 or manifest.get('rules_version') != RULES:
        raise ValueError('Expected materials schema_version=1 and rules_version=r2')
    rows = manifest.get('slices')
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Materials manifest must contain slice entries')
    indexed = {}
    for row in rows:
        name = row.get('name')
        if not isinstance(name, str) or not name or name in indexed:
            raise ValueError('Materials slice names must be present and unique')
        indexed[name] = row
    result = {}
    for source in slices:
        name, version = source['name'], source['version']
        material = indexed.get(name)
        if material is None or material.get('version') != version:
            raise ValueError(f'Materials source name/version mismatch: {name}')
        workspace = (slice_dir / name).resolve()
        if not workspace.is_relative_to(slice_dir):
            raise ValueError('Slice path escapes frozen source directory')
        for key in ('markdown_sha256', 'reader_contract_sha256'):
            digest = material.get(key)
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError(f'Invalid {key}: {name}')
        chapter = material.get('chapter')
        if (not isinstance(chapter, dict) or chapter.get('expectation') not in ('required', 'optional', 'not_required')
                or any(not isinstance(chapter.get(k), str) or not chapter[k].strip() for k in ('reason', 'scope'))):
            raise ValueError(f'Chapter expectation/reason/scope missing: {name}')
        markdown = validate_workspace(workspace, version, material)
        labels = _json(_relative(manifest_path.parent, material.get('labels_file')))
        paraphrases = _json(_relative(manifest_path.parent, material.get('paraphrases_file')))
        _identity(labels, material, 'Labels')
        _identity(paraphrases, material, 'Paraphrases')
        _validate_labels(markdown, labels)
        pairs = _validate_paraphrases(markdown, labels, paraphrases)
        result[name] = {**material, 'labels': labels, 'paraphrases': pairs}
    return result
