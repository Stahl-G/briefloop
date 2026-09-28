"""One mechanical mixed Chinese/English length rule for CLI and UI projections."""
from html.parser import HTMLParser
import re
from markdown_it import MarkdownIt

COUNTING_RULE = '中文汉字每字计 1；连续英文字母或数字串计 1。忽略 Markdown 标记、URL 和 [@source_id] 引用；标题、列表、表格及正文内摘要的文字均计入。'
_CITATION = re.compile(r'\[@[^\]\n]+\]')
_URL = re.compile(r'(?:https?://|www\.)[^\s<>\]\)。，；！？、：“”‘’]+', re.IGNORECASE)
_UNITS = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f]|[A-Za-z0-9]+')


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text=[];self.hidden=0
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style'):self.hidden+=1
        elif tag in ('p','div','br','li','tr'):self.text.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script','style'):self.hidden=max(0,self.hidden-1)
    def handle_data(self, data):
        if not self.hidden:self.text.append(data)


def _visible_text(markdown):
    blocks=[]
    for token in MarkdownIt('commonmark').enable('table').parse(_CITATION.sub('', markdown)):
        if token.type=='inline':
            parts=[]
            for child in token.children or []:
                if child.type in ('text','code_inline','image'):parts.append(child.content)
                elif child.type in ('softbreak','hardbreak'):parts.append('\n')
            blocks.append(''.join(parts))
        elif token.type in ('fence','code_block'):
            blocks.append(token.content)
        elif token.type=='html_block':
            parser=_HTMLText();parser.feed(token.content);blocks.append(''.join(parser.text))
    return _URL.sub('', '\n'.join(blocks))


def count_brief(markdown):
    """Count body units; callers pass Markdown, never the citation metadata JSON."""
    if not isinstance(markdown,str):raise TypeError('正文必须是 Markdown 文本')
    return len(_UNITS.findall(_visible_text(markdown)))


def length_instructions(requirements):
    """One editorial policy for writers, feedback and independent assessments."""
    source = requirements.get('length_requirement') or {}
    if requirements.get('length_mode') == 'strict':
        policy = (f"篇幅模式为 strict：已记录用户明确上限 {requirements.get('max_words')}；"
                  f"要求来源（{source.get('kind','未记录')}）：{source.get('text','未记录')}。"
                  '超过时明确报告实际数量、差距及原要求；优先删重复、离题和无信息量内容，'
                  '不能删除必答问题、事实、引用或采用条件来满足数字。必要内容与限制冲突时说明冲突，保留可编辑、可下载的工作稿。')
    else:
        policy = ('篇幅模式为 soft：target_words 是建议目标，max_words 是建议范围上沿；旧配置同样按软目标解释。'
                  '超出只是一项篇幅建议，不阻断草稿提交，也不能仅凭超出预设判失败、扣分或要求再改一轮。'
                  '按具体重复、离题或阅读负担决定是否精简；必要内容已经完整时可以直接提交。')
    return policy + ('低于目标不自动构成覆盖不足，不为凑字数扩写。只用实际正文计数，不估算；'
                     '摘要、标题和表格计入，来源标记及独立研究记录不计入。'
                     '评价发现需说明具体阅读问题或明确严格要求，不能把篇幅偏离当作事实错误。'
                     '普通工作稿始终可保存、编辑和下载；正式交付沿用证据与审阅门槛。')


def length_stats(markdown, *, target_words=None, max_words=None, length_mode='soft', length_requirement=None):
    for value in (target_words,max_words):
        if value is not None and (type(value) is not int or value<=0):raise ValueError('目标长度与上限必须是正整数')
    if target_words is not None and max_words is not None and max_words<target_words:
        raise ValueError('长度上限不能小于目标长度')
    count=count_brief(markdown)
    mode = 'strict' if length_mode == 'strict' else 'soft'
    return {'count':count,'target_words':target_words,'max_words':max_words,
            'over_limit':count>max_words if max_words is not None else None,
            'over_by':max(0,count-max_words) if max_words is not None else None,
            'length_mode':mode,'length_requirement':length_requirement if mode=='strict' else None,
            'strict_exceeded':bool(mode=='strict' and max_words is not None and count>max_words),
            'limit_scope':'over_limit 和 over_by 只比较传入的结构化 max_words；默认 soft 为篇幅建议，不阻断草稿提交。strict 来自记录的明确用户要求。计数不表示符合全部原始要求、读者约定或后续反馈。',
            'rule':COUNTING_RULE}
