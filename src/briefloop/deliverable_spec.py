"""One saved set of reader requirements shared by writing, evaluation and export."""
import json
import hashlib


def resolve(requirements, template=None):
    sections = requirements.get('sections') or (template or {}).get('sections', [])
    return {'schema_version': 1, 'title': requirements['title'],
            'objective': requirements['objective'], 'audience': requirements.get('audience', ''),
            'writing_mode': requirements.get('writing_mode', 'general'),
            'period': requirements.get('period', ''), 'report_date': requirements.get('report_date', ''),
            'target_words': requirements.get('target_words'), 'max_words': requirements.get('max_words'),
            'template_id': requirements.get('template_id'),
            'sections': sections, 'manual_sections': requirements.get('manual_sections', []), 'key_questions': requirements.get('key_questions', []),
            'writing_preferences': requirements.get('writing_preferences', []),
            'requirement_items': requirement_items(requirements),
            'references': '正文短编号，图表简注，文末精简来源表；详细核查另存'}


def instructions(spec):
    internal = spec.get('writing_mode') == 'internal_report'
    reader = ('这是企业内部报告。直接说明本期变化、对目标组织的业务影响，以及有依据的行动或观察节点。'
              '围绕业务组织自然段落和图表，关键判断放在段首；不要每段套“变化/影响/行动”标签。'
              '专业读者不需要通用背景、免责声明、工具过程或反复“不是…而是…”的修辞。'
              '用准确动词、日期、单位、指标名称和条件表达事实，不删除有信息量的否定或计划性质。'
              if internal else '围绕读者目的取舍信息，写成连贯、直接、有依据的报告。')
    return (reader + '\n本轮产物约定：' + json.dumps(spec, ensure_ascii=False) + '\n'
            '本轮明确要求优先于模板默认，其次是兼容技能及通用默认。模板主章节默认固定，'
            '用户明确要求可增删、改名或重排；sections 已指定的职责、顺序和人工占位必须落实。'
            'mode=manual 的章节只写 placeholder，不开展替代研究，不把它当覆盖不足。'
            'manual_sections 指定的人工填写小节放在对应的已有主章节内，只保留“待填充”，不为此追加新的主章节。'
            '研究缺口、读取失败、来源冲突、计算明细写 research_notes/gaps；不默认在正文追加完整缺口清单。'
            '仅将实质改变主要判断的条件自然写在对应句子中。未完成研究仍按影响评价覆盖，不能靠免责声明加分。')


def research_record(store, brief):
    detail = json.loads(brief['detail']); refs = detail.get('citations', [])
    return {'version_id': brief['id'], 'brief_hash': brief['hash'],
            'notes': detail.get('research_notes', []), 'gaps': detail.get('gaps', []),
            'citations': [{**ref, 'source_name': store.one('sources', ref['source_id'])['name']} for ref in refs],
            'assessments': [json.loads(x['data']) for x in store.rows('SELECT data FROM assessments WHERE version_id=? ORDER BY rowid DESC', (brief['id'],))]}


def requirement_items(requirements):
    items=[]
    for kind,texts in [('objective',[requirements['objective']]),('question',requirements.get('key_questions',[])),('manual',requirements.get('manual_sections',[]))]:
        for text in texts:
            identity='req_'+hashlib.sha256((kind+'\0'+text).encode()).hexdigest()[:20]
            items.append({'requirement_id':identity,'text':text,'mode':'manual' if kind=='manual' else 'required',
                          'origin':'user_requirements','kind':kind})
    return items
