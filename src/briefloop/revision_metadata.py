"""Shared shape contract for revision files and Native metadata tools.

Version identity, actual claims and verbatim body anchors are still checked by
the existing admission paths. A well-shaped array does not resolve a finding.
"""
from .store import dump


def binding_schema():
    return {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
        'required': ['claim_id', 'block_id', 'quote'], 'properties': {
            'claim_id': {'type': 'string', 'minLength': 1, 'description': '本报告接口实际登记的 claim_id，不是 source_id 或数字绑定。'},
            'block_id': {'type': 'string', 'minLength': 1, 'description': '修订正文中真实 blockId。'},
            'quote': {'type': 'string', 'minLength': 1, 'description': '指定正文块中唯一的逐字片段。'}}}}


def response_schema(finding_ids=None):
    schema = {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
        'required': ['finding_id', 'action', 'reason'], 'properties': {
            'finding_id': {'type': 'string', 'minLength': 1},
            'action': {'type': 'string', 'enum': ['corrected', 'removed', 'disagree']},
            'reason': {'type': 'string', 'minLength': 1, 'description': '具体修改或异议依据；此说明不关闭发现。'}}}}
    if finding_ids is not None:
        ids = list(finding_ids)
        schema.update(minItems=len(ids), maxItems=len(ids))
        if ids:
            schema['items']['properties']['finding_id']['enum'] = ids
        else:
            schema['items'] = False
    return schema


def recovery_schema(version_id, brief_hash, finding_ids):
    return {'type': 'object', 'additionalProperties': False,
        'required': ['version_id', 'brief_hash', 'bindings', 'responses'], 'properties': {
            'version_id': {'type': 'string', 'const': version_id},
            'brief_hash': {'type': 'string', 'const': brief_hash},
            'bindings': binding_schema(), 'responses': response_schema(finding_ids)}}


def validate_arrays(bindings, responses, finding_ids):
    """Reject malformed inputs without coercing them or dropping valid history."""
    if not isinstance(bindings, list) or not isinstance(responses, list):
        raise ValueError('修订绑定及处理说明顶层必须为数组；revision_bindings.json 和 responses.json 分别写 [] 或对象数组，不加 bindings/responses 包装对象')
    for binding in bindings:
        if not isinstance(binding, dict) or any(not isinstance(binding.get(key), str) or not binding[key].strip()
                                               for key in ('claim_id', 'block_id', 'quote')):
            raise ValueError('修订绑定缺少 claim_id/block_id/quote；数字定位放 draft.number_bindings')
    response_ids = []
    for item in responses:
        if not isinstance(item, dict) or any(not isinstance(item.get(key), str) or not item[key].strip()
                                            for key in ('finding_id', 'action', 'reason')) or item['action'] not in ('corrected', 'removed', 'disagree'):
            raise ValueError('修订处理说明缺少有效 finding_id/action/reason，均须为字符串')
        response_ids.append(item['finding_id'])
    expected = set(finding_ids)
    if set(response_ids) != expected or len(response_ids) != len(set(response_ids)):
        raise ValueError('修订处理说明须逐项对应本轮发现：' + dump({
            'missing': sorted(expected - set(response_ids)),
            'unexpected': sorted(set(response_ids) - expected),
            'duplicate': sorted({rid for rid in response_ids if response_ids.count(rid) > 1})}))
