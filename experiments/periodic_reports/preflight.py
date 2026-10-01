"""Read-only preflight for a NEW periodic-report protocol, never a model evaluator."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

PROTOCOL = 'briefloop-periodic-reports-v1'
SPLITS = {'development', 'same_series_future', 'cross_series'}


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('timezone required')
    return parsed


def validate(manifest: dict, root: Path) -> dict:
    """Return errors and excluded cases. No files, labels or splits are modified."""
    errors, excluded = [], []
    root = root.resolve()

    def error(where, reason):
        errors.append({'at': where, 'reason': reason})

    if manifest.get('protocol') != PROTOCOL:
        error('protocol', 'unknown protocol; frozen older protocols are not migrated')
    if manifest.get('missing_data_policy') != 'exclude_and_capture_prospectively':
        error('missing_data_policy', 'missing historical inputs must not be reconstructed from final reports')
    comparison = manifest.get('comparison', {})
    for arm in ('baseline', 'candidate'):
        spec = comparison.get(arm, {}) if isinstance(comparison, dict) else {}
        for field in ('code_commit', 'prompt_sha256', 'skills_sha256', 'model', 'effort', 'tool_conditions', 'rules_sha256'):
            value = spec.get(field) if isinstance(spec, dict) else None
            if not isinstance(value, str) or not value.strip():
                error(f'comparison.{arm}.{field}', 'required frozen setting')
            elif field == 'code_commit' and not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', value):
                error(f'comparison.{arm}.{field}', 'full immutable Git commit SHA required')
            elif field.endswith('sha256') and not re.fullmatch('[0-9a-f]{64}', value):
                error(f'comparison.{arm}.{field}', 'expected lowercase SHA-256')
        if not isinstance(spec, dict) or type(spec.get('attempts')) is not int or spec['attempts'] < 1:
            error(f'comparison.{arm}.attempts', 'positive fixed attempt count required; retain failures')
    cases = manifest.get('cases')
    if not isinstance(cases, list) or not cases:
        error('cases', 'nonempty case list required')
        return {'valid': False, 'errors': errors, 'excluded': excluded, 'eligible_cases': 0}
    seen, valid_cases, resources = set(), [], []
    for index, case in enumerate(cases):
        at = f'cases[{index}]'
        if not isinstance(case, dict):
            error(at, 'case must be an object'); continue
        before = len(errors)
        cid = case.get('id')
        if not isinstance(cid, str) or not cid.strip() or cid in seen:
            error(at, 'case id must be nonempty and unique')
        else:
            seen.add(cid)
        for field in ('series_id', 'period', 'requirements', 'missing_data'):
            if not isinstance(case.get(field), str) or not case[field].strip():
                error(f'{at}.{field}', 'nonempty description required')
        if not isinstance(case.get('split'), str) or case.get('split') not in SPLITS:
            error(f'{at}.split', 'unknown split')
        if case.get('origin') not in ('real', 'synthetic'):
            error(f'{at}.origin', 'real or synthetic required')
        if len(errors) != before:
            continue
        if case.get('status') == 'not_backtestable':
            excluded.append({'id': cid, 'reason': case.get('missing_data')})
            continue
        if case.get('status') != 'eligible':
            error(f'{at}.status', 'eligible or not_backtestable required'); continue
        try:
            cutoff = timestamp(case['cutoff'])
            period_end = timestamp(case['period_end'])
        except (ValueError, TypeError, KeyError, AttributeError):
            error(at, 'cutoff and period_end require timezone-aware ISO timestamps'); continue
        if period_end > cutoff:
            error(at, 'historical report period ends after cutoff')
        artifacts = case.get('artifacts')
        if not isinstance(artifacts, list) or not artifacts:
            error(at, 'eligible case needs original input and final-reference artifacts'); continue
        roles = set()
        for n, artifact in enumerate(artifacts):
            loc = f'{at}.artifacts[{n}]'
            if not isinstance(artifact, dict):
                error(loc, 'artifact must be an object'); continue
            role = artifact.get('role')
            if role not in ('input', 'reference_final'):
                error(loc, 'role must be input or reference_final'); continue
            roles.add(role)
            family = artifact.get('disclosure_family')
            if not isinstance(family, str) or not family.strip():
                error(loc, 'documented disclosure_family required for related variants')
            digest = artifact.get('sha256')
            if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
                error(loc, 'expected lowercase SHA-256'); continue
            try:
                relative = Path(artifact['path'])
                path = (root / relative).resolve()
                if relative.is_absolute() or not path.is_relative_to(root) or not path.is_file():
                    raise ValueError('outside root or absent')
                if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    raise ValueError('hash mismatch')
            except (KeyError, TypeError, OSError, ValueError, RuntimeError):
                error(loc, 'artifact must exist within manifest directory and match hash')
            if role == 'input':
                try:
                    if timestamp(artifact['available_at']) > cutoff:
                        error(loc, 'future information after cutoff')
                except (KeyError, TypeError, ValueError, AttributeError):
                    error(loc, 'input availability must be documented with timezone')
                if not isinstance(artifact.get('availability_evidence'), str) or not artifact['availability_evidence'].strip():
                    error(loc, 'availability evidence required; a hash is not a historical timestamp')
            resources.append((case.get('split'), cid, role, digest, family))
        if roles != {'input', 'reference_final'}:
            error(at, 'both original input and separately held final-reference artifacts required')
        valid_cases.append((case, cutoff, period_end))
    # Explicit grouping is intentional: never silently reshuffle a frozen dataset.
    development = [(c, t, p) for c, t, p in valid_cases if c.get('split') == 'development']
    dev_series = {c.get('series_id') for c, _, _ in development}
    for c, cutoff, period_end in valid_cases:
        series = c.get('series_id')
        if c.get('split') == 'cross_series' and series in dev_series:
            error(c.get('id'), 'cross-series holdout shares development series')
        if c.get('split') == 'same_series_future':
            prior = [(t, p) for d, t, p in development if d.get('series_id') == series]
            if not prior or any(t >= cutoff or p >= period_end for t, p in prior):
                error(c.get('id'), 'same-series future must follow every development cutoff and period')
    for i, (split, cid, role, digest, family) in enumerate(resources):
        for other_split, other_id, other_role, other_digest, other_family in resources[:i]:
            same = digest == other_digest or (family and family == other_family)
            if same and ((role != other_role) or (split != other_split)):
                error(cid, f'artifact overlaps {other_id}: reference/input or split leakage')
    if not development:
        error('cases', 'at least one eligible development case required')
    if not any(c.get('split') != 'development' for c, _, _ in valid_cases):
        error('cases', 'at least one eligible holdout case required')
    return {'valid': not errors, 'errors': errors, 'excluded': excluded,
            'eligible_cases': len(valid_cases), 'synthetic_cases': sum(c.get('origin') == 'synthetic' for c, _, _ in valid_cases),
            'claim': 'preflight only; no quality gain or human rework reduction established'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.manifest.read_text())
        if not isinstance(data, dict):
            raise ValueError('manifest must be an object')
        result = validate(data, args.manifest.parent)
    except (OSError, ValueError) as exc:
        result = {'valid': False, 'errors': [{'at': 'manifest', 'reason': str(exc)}]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
