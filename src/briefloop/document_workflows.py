"""Versioned document methods, selected once and stored with the run.

This registry supplies instructions to the existing agents; it does not author
reports, change tools or grant research permissions. Historical consumers only
read the saved snapshot, never today's installed assets.
"""
from functools import lru_cache
import hashlib
import json
from pathlib import Path

ASSETS = Path(__file__).with_name('workflow_assets')
WORKFLOW_IDS = ('general_report', 'business_report')
ROLE_KEYS = {'orchestrator': 'planning', 'scout': 'planning', 'analyst': 'writing',
             'revision': 'writing', 'evaluator': 'evaluation', 'reviewer': 'evaluation'}


def list_workflows():
    return [json.loads((ASSETS / identity / 'manifest.json').read_text()) for identity in WORKFLOW_IDS]


def resolve_workflow(requirements, template_hint=None):
    identity = requirements.get('workflow_id')
    variant = requirements.get('workflow_variant')
    if variant and not identity:
        raise ValueError('请选择文档类型后再指定用途')
    origin = 'explicit' if identity else 'default'
    if not identity:
        if requirements.get('report_profile') == 'industry_periodic':
            identity, variant, origin = 'business_report', 'industry_periodic', 'legacy_profile'
        elif template_hint in WORKFLOW_IDS:
            identity, origin = template_hint, 'template_suggestion'
        else:
            identity = 'general_report'
    catalog = {item['id']: item for item in list_workflows()}
    if identity not in catalog:
        raise ValueError('尚未提供此文档生产方法：' + str(identity))
    manifest = catalog[identity]
    variant = variant or manifest['default_variant']
    if variant not in {item['id'] for item in manifest['variants']}:
        raise ValueError('该文档类型不支持所选用途：' + str(variant))
    return {'id': identity, 'variant': variant, 'selection_origin': origin}


def freeze_workflow(selection):
    manifest = next(item for item in list_workflows() if item['id'] == selection['id'])
    root = ASSETS / manifest['id']
    variant = next(item for item in manifest['variants'] if item['id'] == selection['variant'])
    roles = {key: (root / (key + '.md')).read_text().strip() for key in ('planning', 'writing', 'evaluation')}
    methods = {identity: (root / 'methods' / (identity + '.md')).read_text().strip()
               for identity in variant.get('method_ids', [])}
    snapshot = {'schema_version': 1, **selection, 'label': manifest['label'],
                'variant_label': variant['label'], 'version': manifest['version'],
                'role_instructions': roles, 'methods': methods,
                'method_roles': manifest.get('method_roles', {})}
    snapshot['content_hash'] = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False,
                                                       sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return snapshot


def workflow_context(snapshot, role):
    if not snapshot:
        return ''
    key = ROLE_KEYS[role]
    parts = [f"本轮文档方法：{snapshot['label']} · {snapshot['variant_label']}（{snapshot['version']}）。用户明确要求优先。",
             snapshot['role_instructions'][key]]
    # Reviewers inspect the artifact against the evaluation standard; author
    # research procedures are not commands to perform new work during review.
    for identity, text in snapshot.get('methods', {}).items():
        if key in snapshot.get('method_roles', {}).get(identity, ['planning', 'writing']):
            parts.append(text)
    return '\n'.join(parts)


@lru_cache(maxsize=1)
def _template_hints():
    from .templates import BUILTIN_TEMPLATES
    root = Path(__file__).with_name('template_assets')
    hints = {}
    for filename, _, _ in BUILTIN_TEMPLATES:
        family = next((name for name in ('business-report', 'general-report') if filename.startswith(name)), None)
        if family:
            hints[hashlib.sha256((root / filename).read_bytes()).hexdigest()] = family.replace('-', '_')
    return hints


def template_workflow_hint(template):
    if not template or template.get('origin') != 'builtin':
        return None
    return _template_hints().get(template.get('source_hash'))
