"""One mechanical mixed Chinese/English length rule for CLI and UI projections."""
from html.parser import HTMLParser
import re
from markdown_it import MarkdownIt

COUNTING_RULE = '中文汉字每字计 1；连续英文字母或数字串计 1。忽略 Markdown 标记、URL 和 [@source_id] 引用；标题、列表和表格中的文字计入正文。'
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


def length_stats(markdown, *, target_words=None, max_words=None):
    for value in (target_words,max_words):
        if value is not None and (type(value) is not int or value<=0):raise ValueError('目标长度与上限必须是正整数')
    if target_words is not None and max_words is not None and max_words<target_words:
        raise ValueError('长度上限不能小于目标长度')
    count=count_brief(markdown)
    return {'count':count,'target_words':target_words,'max_words':max_words,
            'over_limit':count>max_words if max_words is not None else None,
            'over_by':max(0,count-max_words) if max_words is not None else None,
            'rule':COUNTING_RULE}
