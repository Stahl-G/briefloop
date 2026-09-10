"""Native-friendly fonts for new generic documents, never for user templates.

Arial supplies Western text. Chinese has no fixed cross-platform family name
here and is left to the reader's installed-font fallback. This does not promise
identical glyphs or pagination across operating systems and office applications.
"""
from io import BytesIO
from zipfile import ZipFile
import re

from lxml import etree

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
M = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
NS = {'w': W, 'a': A, 'm': M}
WESTERN_FONT = 'Arial'


def native_default_fonts(payload,*,language=None):
    """Finalize only a fresh default DOCX, including its unused stock styles."""
    result = BytesIO()
    with ZipFile(BytesIO(payload)) as source, ZipFile(result, 'w') as output:
        selected=language.strip().lower().replace('_','-') if isinstance(language,str) else ''
        simplified=selected in {'中文','简体','简体中文','zh','zh-cn','zh-hans','zh-hans-cn','chinese','simplified chinese'}
        if not selected:
            body=etree.fromstring(source.read('word/document.xml'))
            simplified=any(re.search(r'[\u3400-\u4dbf\u4e00-\u9fff]',node.text or '') for node in body.findall('.//w:t',NS))
        for member in source.infolist():
            blob = source.read(member.filename)
            if member.filename.startswith('word/') and member.filename.endswith('.xml'):
                root = etree.fromstring(blob)
                if simplified:
                    # The stock document can carry ja-JP in its theme hints.
                    # Locale guides CJK fallback without naming an absent font.
                    for node in root.findall('.//w:lang',NS)+root.findall('.//w:themeFontLang',NS):
                        node.set('{'+W+'}eastAsia','zh-CN')
                    defaults=root.find('w:docDefaults/w:rPrDefault/w:rPr',NS)
                    if defaults is not None and defaults.find('w:lang',NS) is None:
                        etree.SubElement(defaults,'{'+W+'}lang').set('{'+W+'}eastAsia','zh-CN')
                # python-docx's stock Symbol bullet is U+F0B7. Merely changing
                # its font to Arial would turn it into a missing-glyph box.
                for level in root.findall('.//w:lvl', NS):
                    kind = level.find('w:numFmt', NS)
                    text = level.find('w:lvlText', NS)
                    if kind is not None and kind.get('{'+W+'}val') == 'bullet' and text is not None:
                        text.set('{'+W+'}val', '\u2022')
                for fonts in root.findall('.//w:rFonts', NS):
                    for key in list(fonts.attrib):
                        if etree.QName(key).localname != 'hint':
                            del fonts.attrib[key]
                    for key in ('ascii', 'hAnsi', 'cs'):
                        fonts.set('{'+W+'}'+key, WESTERN_FONT)
                # Unused fontTable/theme declarations can still trigger WPS's
                # missing-font notification, even with readable body text.
                for font in root.findall('.//w:font', NS):
                    if font.get('{'+W+'}name') != WESTERN_FONT:
                        font.getparent().remove(font)
                for font in root.findall('.//m:mathFont', NS):
                    # Do not label Arial as a mathematical typesetting font.
                    # Omit the stock choice and let the office reader decide.
                    font.getparent().remove(font)
                for scheme in root.findall('.//a:fontScheme', NS):
                    for family in list(scheme):
                        for font in list(family):
                            tag = etree.QName(font.tag).localname
                            if tag == 'font':
                                family.remove(font)
                            elif tag == 'latin':
                                font.set('typeface', WESTERN_FONT)
                            elif tag in ('ea', 'cs'):
                                font.set('typeface', '')
                blob = etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
            output.writestr(member, blob)
    return result.getvalue()
