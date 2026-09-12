"""Build the bundled built-in report template (docx + preparation spec).

Run from the repo root:
    .venv/bin/python scripts/build_builtin_template.py

The template must stay a single-section DOCX (templates.import_template rejects
multi-section files). Cover paragraphs carry {{title}}/{{organization}}/
{{period}}/{{report_date}} placeholders; chapter headings exist only as style
anchors and are removed by prepare(); the sample table exists only so
table_defaults() captures its three-line borders. The spec records body-block
indices, so any structural edit here must be regenerated together with the
spec — both files are written in one run.
"""
import json
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor

ASSETS = Path(__file__).resolve().parent.parent / 'src' / 'briefloop' / 'template_assets'
INK = '1E2320'
GREEN = '006838'
MUTED = '6A706B'

SECTIONS = (
    ('summary', '摘要', '本期核心结论'),
    ('dynamics', '行业动态', '事件与政策回顾'),
    ('data', '数据追踪', '关键指标与前值'),
    ('analysis', '分析研判', '影响推演与观点'),
    ('risks', '风险提示', '需要关注的不确定性'),
    ('appendix', '附录', '方法、缺口与来源说明'),
)


def set_east_asia(font_element_owner, name):
    rpr = font_element_owner.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn('w:eastAsia'), name)


def style_fonts(style, *, size, bold=False, color=INK, east='宋体', western='Arial'):
    style.font.name = western
    style.font.size = Pt(size)
    style.font.bold = bold
    style.font.color.rgb = RGBColor.from_string(color)
    set_east_asia(style.element, east)


def customize_styles(doc):
    normal = doc.styles['Normal']
    style_fonts(normal, size=10.5)
    normal.paragraph_format.line_spacing = 1.4
    for name, size in (('Heading 1', 15), ('Heading 2', 12.5), ('Heading 3', 11)):
        style = doc.styles[name]
        style_fonts(style, size=size, bold=True, color=INK, east='黑体')
        style.font.color.rgb = RGBColor.from_string(INK)


def add_cover_paragraph(doc, text, *, size, bold=False, color=INK, space_before=0):
    paragraph = doc.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.space_before = Pt(space_before)
    if text:
        run = paragraph.add_run(text)
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = RGBColor.from_string(color)
        set_east_asia(run._r, '黑体' if bold else '宋体')
    return paragraph


def add_page_break(doc):
    paragraph = doc.add_paragraph()
    paragraph.add_run().add_break(WD_BREAK.PAGE)
    return paragraph


def add_chapter(doc, label):
    paragraph = doc.add_paragraph(style='Heading 1')
    paragraph.add_run(label)
    return paragraph


def add_sample_table(doc):
    table = doc.add_table(rows=3, cols=3)
    table.alignment = 1
    borders = OxmlElement('w:tblBorders')
    for edge, value, size in (('top', 'single', '12'), ('bottom', 'single', '12'),
                              ('left', 'none', '0'), ('right', 'none', '0'),
                              ('insideH', 'none', '0'), ('insideV', 'none', '0')):
        element = OxmlElement('w:' + edge)
        element.set(qn('w:val'), value)
        element.set(qn('w:sz'), size)
        element.set(qn('w:color'), INK)
        borders.append(element)
    table._tbl.tblPr.append(borders)
    for column, width in enumerate((40, 60, 60)):
        table.columns[column].width = Mm(width)
    for r, row in enumerate(table.rows):
        for c, cell in enumerate(row.cells):
            cell.paragraphs[0].add_run(f'示例{r+1}-{c+1}')
            if r == 0:
                cell.paragraphs[0].runs[0].font.bold = True
                cell_borders = OxmlElement('w:tcBorders')
                bottom = OxmlElement('w:bottom')
                bottom.set(qn('w:val'), 'single')
                bottom.set(qn('w:sz'), '6')
                bottom.set(qn('w:color'), INK)
                cell_borders.append(bottom)
                cell._tc.get_or_add_tcPr().append(cell_borders)
    return table


def add_toc_block(doc):
    label = doc.add_paragraph()
    label.paragraph_format.space_before = Pt(6)
    label.paragraph_format.space_after = Pt(10)
    run = label.add_run('目录')
    run.bold = True
    run.font.size = Pt(15)
    run.font.color.rgb = RGBColor.from_string(INK)
    set_east_asia(run._r, '黑体')
    holder = doc.add_paragraph()
    field = OxmlElement('w:fldSimple')
    field.set(qn('w:instr'), ' TOC \\o "1-3" \\h \\z \\u ')
    placeholder = OxmlElement('w:r')
    placeholder_text = OxmlElement('w:t')
    placeholder_text.text = '目录将在打开文档时自动生成'
    placeholder.append(placeholder_text)
    field.append(placeholder)
    holder._p.append(field)


def add_header_footer(section):
    section.different_first_page_header_footer = True
    header = section.header
    header_paragraph = header.paragraphs[0]
    header_paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    header_run = header_paragraph.add_run('{{title}}')
    header_run.font.size = Pt(8)
    header_run.font.color.rgb = RGBColor.from_string(MUTED)
    set_east_asia(header_run._r, '宋体')
    border = OxmlElement('w:pBdr')
    bottom = OxmlElement('w:bottom')
    for key, value in (('val', 'single'), ('sz', '4'), ('color', 'DEDFD8')):
        bottom.set(qn('w:' + key), value)
    border.append(bottom)
    header_paragraph._p.get_or_add_pPr().append(border)
    first_header = section.first_page_header
    first_header.paragraphs[0].text = ''
    first_footer = section.first_page_footer
    first_footer.paragraphs[0].text = ''
    footer = section.footer
    footer_paragraph = footer.paragraphs[0]
    footer_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer_paragraph.add_run('第 ')
    field = OxmlElement('w:fldSimple')
    field.set(qn('w:instr'), ' PAGE ')
    field_run = OxmlElement('w:r')
    field_text = OxmlElement('w:t')
    field_text.text = '1'
    field_run.append(field_text)
    field.append(field_run)
    footer_paragraph._p.append(field)
    footer_paragraph.add_run(' 页')


def build():
    doc = Document()
    customize_styles(doc)
    add_header_footer(doc.sections[0])
    add_cover_paragraph(doc, '{{organization}}', size=11, color=MUTED, space_before=90)
    title = add_cover_paragraph(doc, '{{title}}', size=24, bold=True, space_before=24)
    title.style = doc.styles['Heading 1']
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_cover_paragraph(doc, '{{period}} · {{report_date}}', size=11, color=MUTED, space_before=16)
    add_page_break(doc)
    add_toc_block(doc)
    add_page_break(doc)
    numerals = ('一', '二', '三', '四', '五', '六')
    for (sid, title, purpose), numeral in zip(SECTIONS, numerals):
        add_chapter(doc, numeral + '、' + title)
    body_sample = doc.add_paragraph(
        '这是一段正文样式示例：BriefLoop 会把该段的字体、字号、行距与首行格式作为报告正文样式。'
        'Numbers like 1,234.56 keep Arial while 汉字落到中文字体。')
    add_sample_table(doc)

    blocks = list(doc.element.body)
    def index_of(predicate, label):
        for index, element in enumerate(blocks):
            if element.tag == qn('w:p') and predicate(''.join(t.text or '' for t in element.iter(qn('w:t')))):
                return index
        raise SystemExit('模板缺少锚点：' + label)

    sections = [
        {'section_id': sid, 'title': title, 'index': index_of(lambda t, l=title: t.endswith('、' + l), title), 'purpose': purpose}
        for sid, title, purpose in SECTIONS
    ]
    first_section = min(s['index'] for s in sections)
    keep_blocks = [index for index, element in enumerate(blocks)
                   if element.tag == qn('w:p') and index < first_section]
    spec = {
        'sections': sections,
        'keep_blocks': keep_blocks,
        'paragraph_index': index_of(lambda t: t.startswith('这是一段正文样式示例'), '正文样例'),
        'fields': [
            {'old': '{{title}}', 'field': 'title'},
            {'old': '{{organization}}', 'field': 'organization'},
            {'old': '{{period}}', 'field': 'period'},
            {'old': '{{report_date}}', 'field': 'report_date'},
        ],
    }
    if sorted(set(spec['keep_blocks'])) != spec['keep_blocks'] or max(spec['keep_blocks']) >= min(s['index'] for s in spec['sections']):
        raise SystemExit('封面块索引必须小于全部章节索引')
    ASSETS.mkdir(parents=True, exist_ok=True)
    doc.save(ASSETS / 'research-report-zh.docx')
    (ASSETS / 'research-report-zh.spec.json').write_text(
        json.dumps(spec, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({'sections': len(spec['sections']), 'keep_blocks': spec['keep_blocks'],
                      'paragraph_index': spec['paragraph_index'], 'blocks': len(blocks)}, ensure_ascii=False))


if __name__ == '__main__':
    sys.exit(build())
