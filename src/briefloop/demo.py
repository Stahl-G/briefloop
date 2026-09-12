"""A saved, explicitly synthetic example. Never invokes or impersonates an agent."""
from .sources import upload
from .store import uid, content_hash, dump
import threading
import json

_IMPORT_LOCK = threading.Lock()


SOURCE = '''合成材料，不描述真实公司，也不代表实际核验结果。
周期：2026-09-01 至 2026-09-07；对比周期：2026-08-25 至 2026-08-31。
本周交付 120 件，上周 100 件。
本周逾期订单 6 单，上周 10 单。
客户反馈平均响应时间本周 8 小时，上周 12 小时。
团队将交付改善归因于新增包装工位；仍有 6 单逾期，均在等待包装材料。
负责人已安排下周三核对包装材料到货、下周五检查逾期单清理情况；尚无完成记录。
'''


def create_demo(store):
    # The local HTTP server admits requests on separate threads. Serialize the
    # check and import; persist identities so a process restart can resume too.
    with _IMPORT_LOCK:
        return _create_demo(store)


def _create_demo(store):
    previous = store.meta('demo')
    if previous:
        store.one('briefs', previous['version_id'])
        return previous
    pending = store.meta('demo_import')
    if not pending:
        if any(store.rows('SELECT 1 FROM '+table+' LIMIT 1') for table in ('sources', 'runs', 'jobs')):
            raise ValueError('示例仅可导入空工作区；请新建工作区以保留现有材料与设置')
        pending = {'marker': uid('example'), 'version_id': uid('brief')}
        store.set_meta('demo_import', pending)
    # With no jobs or sources, this only changes the new example workspace.
    # Choosing a real model and enabling learning remain explicit user actions.
    settings = store.settings()
    if settings.get('model_selection_required'):
        settings['model'] = ''  # Do not treat the factory suggestion as consent.
    store.set_meta('settings', {**settings, 'auto_learn': False})
    name = '合成团队周报材料.txt'
    existing = store.rows('SELECT * FROM sources WHERE name=? AND hash=?', (name, content_hash(SOURCE)))
    source = existing[0] if existing else upload(store, name, SOURCE.encode('utf-8'))
    sid = source['id']
    requirements = {'title': '合成示例：团队交付周报',
        'objective': '依据合成材料说明本周变化、影响与下一步；区分归因判断和已发生事实。',
        'audience': '团队负责人', 'period': '2026-09-01 至 2026-09-07',
        'target_words': 350, 'max_words': 500, 'allow_web': False,
        'raw_input': '内置合成示例 ' + pending['marker']}
    existing = [row for row in store.rows('SELECT * FROM runs WHERE source_ids=?', (dump([sid]),))
                if json.loads(row['requirements']).get('raw_input') == requirements['raw_input']]
    run = existing[0] if existing else store.create_run(requirements, [sid])
    body = f'''# 合成示例：团队交付周报

> 这是随软件提供的示例稿，未调用模型，未评分或独立审阅。可查看来源、编辑并导出工作稿；不代表正式交付通过。

## 本周变化

本期覆盖 2026-09-01 至 2026-09-07。本周交付 120 件，高于上周 100 件；逾期订单从 10 单降至 6 单；客户反馈平均响应时间从 12 小时缩短至 8 小时。[@{sid}]

| 指标 | 本周 | 上周 |
| --- | --- | --- |
| 交付量 | 120 件 | 100 件 |
| 逾期订单 | 6 单 | 10 单 |
| 平均响应时间 | 8 小时 | 12 小时 |

## 影响

三项指标同时改善。团队将交付改善归因于新增包装工位；这属于材料中的解释，单靠前后对比尚不能确认因果关系。剩余 6 单均在等待包装材料。[@{sid}]

## 下一步

负责人已安排下周三核对包装材料到货、下周五检查逾期单清理情况。这是计划，材料尚未记录完成结果。[@{sid}]
'''
    brief = store.publish(run['id'], {'title': '合成示例：团队交付周报', 'markdown': body,
        'citations': [{'source_id': sid, 'locator': 'lines 1-7'}]}, author='example', version_id=pending['version_id'])
    result = {'run_id': run['id'], 'version_id': brief['id'], 'source_id': sid,
              'notice': '合成示例已保存；未调用模型或评分，自动学习已关闭。'}
    store.set_meta('demo', result)
    return result
