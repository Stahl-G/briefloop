"""Shared user-question contract, separate from tool permission decisions."""


def normalize_questions(questions):
    if not isinstance(questions, list) or not questions or len(questions) > 20:
        raise ValueError('提问格式无效')
    result = []
    seen = set()
    for question in questions:
        if not isinstance(question, dict):
            raise ValueError('提问格式无效')
        key = question.get('id')
        text = question.get('question')
        if not isinstance(key, str) or not key or key in seen or not isinstance(text, str) or not text.strip():
            raise ValueError('问题缺少正文或唯一标识')
        seen.add(key)
        options = question.get('options') or []
        if not isinstance(options, list) or len(options) > 100:
            raise ValueError('问题选项格式无效')
        normalized = []
        for option in options:
            if not isinstance(option, dict) or not isinstance(option.get('label'), str) or not option['label'].strip():
                raise ValueError('问题选项格式无效')
            normalized.append({'label': option['label'], 'description': str(option.get('description') or '')})
        editor = question.get('inputType') == 'editor'
        result.append({'id': key, 'question': text, 'header': str(question.get('header') or ''),
                       'options': normalized, 'multiSelect': question.get('multiSelect') is True,
                       'allowCustom': not normalized or question.get('allowCustom', question.get('isOther', True)) is not False,
                       **({'inputType':'editor','prefill':question.get('prefill') if isinstance(question.get('prefill'),str) else ''} if editor else {})})
    return result


def validate_answers(questions, answers):
    """Keep selected labels and custom text; never turn answers into approvals."""
    questions = normalize_questions(questions)
    if not isinstance(answers, dict) or set(answers) != {q['id'] for q in questions}:
        raise ValueError('请回答每一个问题；回答不能包含未知问题')
    result = {}
    for question in questions:
        value = answers[question['id']]
        values = value.get('answers') if isinstance(value, dict) else value
        if isinstance(values, str):
            values = [values]
        if question.get('inputType')=='editor':
            if not isinstance(values,list) or len(values)!=1 or not isinstance(values[0],str):
                raise ValueError('编辑内容必须为文字')
            result[question['id']]={'answers':values}
            continue
        if not isinstance(values, list) or not values or len(values) > 100:
            raise ValueError('回答必须为文字')
        if not all(isinstance(v, str) and v.strip() and len(v) <= 10000 for v in values):
            raise ValueError('请填写有效回答')
        values = list(dict.fromkeys(v.strip() for v in values))
        if not question['multiSelect'] and len(values) != 1:
            raise ValueError('该问题只能选择一个答案')
        if not question['allowCustom'] and any(v not in {o['label'] for o in question['options']} for v in values):
            raise ValueError('请选择问题提供的选项')
        result[question['id']] = {'answers': values}
    return result
