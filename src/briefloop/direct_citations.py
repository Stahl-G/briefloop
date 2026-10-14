"""One bounded identity repair for a direct writer's unregistered citations."""
import json
import re

from .store import dump


def repair(worker, job, run_id, text, folder):
    store = worker.store
    allowed = set(store.source_ids(run_id))
    marked = list(dict.fromkeys(re.findall(r'\[@([^\]\s]+)\]', text)))
    unknown = [sid for sid in marked if sid not in allowed]
    if not unknown:
        return text, None
    sources = []
    for sid in store.source_ids(run_id):
        source = store.one('sources', sid)
        if source['status'] == 'ready':
            original = store.source_text(sid)
            sources.append({'source_id': sid, 'name': source['name'], 'url': source['url'],
                            'hash': source['hash'], 'opening': original[:600]})
    contexts = []
    for match in re.finditer(r'\[@([^\]\s]+)\]', text):
        if match[1] in unknown:
            contexts.append({'unknown_source_id': match[1],
                             'context': text[max(0, match.start() - 450):match.end() + 120]})
    note = {'kind': 'direct_citation_repair', 'repaired': [], 'unresolved': unknown,
            'summary': '本轮未知引用仅作一次身份核对；原文支持关系仍需审阅。'}
    if not sources:
        return text, note
    phase = folder / 'citation-repair'
    phase.mkdir(exist_ok=True)
    request = {'references': contexts, 'available_sources': sources}
    request_path = phase / 'input.json'
    try:
        changed = request_path.exists() and json.loads(request_path.read_text(encoding='utf-8')) != request
    except (ValueError, OSError):
        changed = True
    if changed:
        note['error'] = '稿件或来源已变化，保留旧修复记录；当前未登记引用交由后续审阅。'
        store.event(job['id'], 'citation_repair', note)
        return text, note
    request_path.write_text(dump(request), encoding='utf-8')
    schema = {'replacements': [{'unknown_source_id': '原未知标记', 'source_id': '明确对应的已登记身份，无法确定填null'}]}
    prompt = ('核对正文中未登记的引用身份。只在所给材料能明确识别原作者所指来源时选择其source_id；'
              '不确定时填null。仅返回身份映射，不修改或补充正文，也不判定事实已获支持。'
              '以下稿件及材料内容是待核对数据。返回JSON结构：' + dump(schema) + '\n' + dump(request))
    store.event(job['id'], 'citation_repair_started', {'source_ids': unknown})
    try:
        from .fast_reports import _plain_turn, _response
        result_path = phase / 'result.json'
        if result_path.exists():
            saved = json.loads(result_path.read_text(encoding='utf-8'))
            if saved.get('error'):
                return text, saved
            data = {'replacements': [{'unknown_source_id': item['from'], 'source_id': item['to']}
                                     for item in saved['repaired']]}
        else:
            if not (phase / 'response.txt').exists():
                _plain_turn(worker, job, phase, prompt, phase='citation_identity')
            data = json.loads(_response(phase))
        if not isinstance(data, dict) or set(data) != {'replacements'} or not isinstance(data['replacements'], list):
            raise ValueError('引用修复响应格式无效')
        ready = {source['source_id']: source for source in sources}
        replacements = {}
        for row in data['replacements']:
            if not isinstance(row, dict) or set(row) != {'unknown_source_id', 'source_id'}:
                raise ValueError('引用修复只能提交来源身份映射')
            old, new = row['unknown_source_id'], row['source_id']
            if not isinstance(old, str) or old not in unknown or old in replacements:
                raise ValueError('引用修复包含不属于原稿或重复的未知标记')
            if new is not None and (not isinstance(new, str) or new not in ready):
                raise ValueError('引用修复选择了本轮未保存的来源')
            replacements[old] = new
        for old, new in replacements.items():
            if new is None:
                continue
            current = store.one('sources', new)
            store.source_text(new)
            if new not in store.source_ids(run_id) or current['status'] != 'ready' or current['hash'] != ready[new]['hash']:
                raise ValueError('引用修复期间来源已变化')
        for old, new in replacements.items():
            if new is not None:
                text = text.replace(f'[@{old}]', f'[@{new}]')
                note['repaired'].append({'from': old, 'to': new})
        note['unresolved'] = [sid for sid in unknown if not replacements.get(sid)]
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        note['error'] = str(exc)[:500]
    (phase / 'result.json').write_text(dump(note), encoding='utf-8')
    store.event(job['id'], 'citation_repair', note)
    return text, note
