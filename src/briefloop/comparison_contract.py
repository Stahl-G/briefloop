"""Caller-owned pairwise result extensions, checked before a run can settle.

Ordinary learning comparisons need no extension. A caller may freeze required
pair fields and bindings to its input cases; the model cannot relax them by
omitting an output field or editing a schema in its reply.
"""
from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft7Validator
from pydantic import BaseModel, ConfigDict, Field


class Binding(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    result_path: list[str] = Field(min_length=1)
    case_path: list[str] = Field(min_length=1)


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    pair_fields: dict[str, dict]
    definitions: dict[str, dict] = Field(default_factory=dict)
    bindings: list[Binding] = Field(default_factory=list)


def extend_schema(base, value):
    """Only add fields: the shared comparison shape cannot be weakened."""
    contract = Contract.model_validate(value)
    schema = deepcopy(base)
    pair = schema['properties']['pairs']['items']
    for name, field in contract.pair_fields.items():
        if not name or name in pair['properties']:
            raise ValueError('比较扩展字段不能为空或覆盖基础字段')
        pair['properties'][name] = deepcopy(field)
        pair['required'].append(name)
    schema['$defs'] = deepcopy(contract.definitions)

    def local_refs(node):
        if isinstance(node, dict):
            if '$id' in node or '$dynamicRef' in node:
                raise ValueError('比较契约不支持 $id 或 $dynamicRef')
            if '$ref' in node and (not isinstance(node['$ref'], str) or not node['$ref'].startswith('#')):
                raise ValueError('比较契约只允许本地 JSON Schema 引用')
            for child in node.values():
                local_refs(child)
        elif isinstance(node, list):
            for child in node:
                local_refs(child)
    local_refs(schema)
    Draft7Validator.check_schema(schema)
    return schema


def frozen_contract(config):
    value = config.get('comparison_contract')
    path = Path(config['packet_root']) / 'submission-contract.json' if config.get('packet_root') else None
    if value is None:
        if path is not None and path.exists():
            raise ValueError('冻结任务包含比较提交契约，但任务配置未绑定该契约')
        return None
    if path is None or not path.is_file() or json.loads(path.read_text(encoding='utf-8')) != value:
        raise ValueError('比较提交契约与冻结任务包不一致，不能接纳结果')
    return value


def validate_result(base, value, result, comparisons):
    schema = extend_schema(base, value)
    errors = [f"{'/'.join(map(str, e.absolute_path)) or '/'}: {e.message}"
              for e in Draft7Validator(schema).iter_errors(result)]
    contract = Contract.model_validate(value)
    cases = {case['case_id']: case for case in comparisons}

    def get_path(obj, path):
        for key in path:
            if not isinstance(obj, dict) or key not in obj:
                raise KeyError(key)
            obj = obj[key]
        return obj

    for i, pair in enumerate(result['pairs']):
        for binding in contract.bindings:
            location = f"pairs/{i}/{'/'.join(binding.result_path)}"
            try:
                expected = get_path(cases[pair['case_id']], binding.case_path)
            except KeyError:
                raise ValueError('比较契约指定的输入绑定不存在：' + '/'.join(binding.case_path)) from None
            try:
                actual = get_path(pair, binding.result_path)
            except KeyError:
                errors.append(location + ': 缺少绑定字段')
                continue
            if type(actual) is not type(expected) or actual != expected:
                errors.append(location + ': 与冻结案例输入不匹配')
    if errors:
        raise ValueError('提交未完整；请修复以下字段后重新提交（本次未保存）：\n' + '\n'.join(errors[:16]))
