"""Text a figure's script draws is listed for the Reviewer without running it."""
import json

from briefloop.figure_text import figure_texts, python_texts
from briefloop.review import build_packet
from briefloop.store import Store

SCRIPT = '''from PIL import Image, ImageDraw, ImageFont
F = ImageFont.truetype("/System/Library/Fonts/Hiragino Sans GB.ttc", 20)
img = Image.new("RGB", (400, 200), "white"); d = ImageDraw.Draw(img)
d.text((10, 10), "TOYO 8月日成交量", font=F, fill="#333333")
d.text((10, 40), f"8/19 量比 {ratio:.2f}x", font=F)
d.text((10, 170), "8月无单日量比≥1.5x的放量交易日", font=F)
plt_title = "Revenue by quarter"
mode = "RGB"
'''


def test_display_strings_come_with_line_numbers_and_the_rest_is_dropped():
    assert python_texts(SCRIPT) == [
        {'line': 4, 'text': 'TOYO 8月日成交量'},
        {'line': 5, 'text': '8/19 量比 {…}x'},
        {'line': 6, 'text': '8月无单日量比≥1.5x的放量交易日'},
        {'line': 7, 'text': 'Revenue by quarter'},
    ]


def test_figures_without_a_readable_script_say_to_look_at_the_image(tmp_path):
    for name, blob in ((None, b''), ('script.sh', b'echo reuse'), ('script.py', b'def (')):
        result = figure_texts(name, blob)
        assert result['text_source'] == 'none' and result['texts'] == [] and '看图' in result['note']
    # The script is parsed, never executed.
    marker = tmp_path / 'ran'
    figure_texts('script.py', f'open({str(marker)!r}, "w").write("x")\n'.encode())
    assert not marker.exists()


def test_packet_lists_figure_text_next_to_data_and_image(tmp_path):
    from PIL import Image
    from briefloop.figures import register_figure
    store = Store(tmp_path)
    source = store.add_source('Synthetic', 'Daily volume ratio was 6.87x on 19 Aug.')
    run = store.create_run({'title': 'T', 'objective': 'o'}, [source['id']])
    image = store.root / 'chart.png'
    Image.new('RGB', (30, 30), 'white').save(image)
    data = store.root / 'data.csv'
    data.write_text('date,ratio\n2026-08-19,6.87\n2026-08-20,1.57\n')
    script = store.root / 'chart.py'
    script.write_text(SCRIPT, encoding='utf-8')
    saved = register_figure(store, run['id'], image, '日量比', '8月日量比', [source['id']], data, script)
    brief = store.publish(run['id'], {'title': 'T', 'markdown': '8月有3个放量日。\n\n' + saved['markdown']})
    _, files = build_packet(store, brief['id'], store.root / 'review')
    assert 'figure-texts.json' in files
    listed = json.loads((store.root / 'review/packet/figure-texts.json').read_text(encoding='utf-8'))
    assert len(listed) == 1
    entry = listed[0]
    assert entry['figure_id'] == saved['figure_id'] and entry['text_source'] == 'script'
    assert entry['data_file'].endswith('data.csv') and entry['image'].endswith('image.png')
    assert {'line': 6, 'text': '8月无单日量比≥1.5x的放量交易日'} in entry['texts']


def test_packet_views_split_the_snapshot_by_purpose(tmp_path):
    from briefloop.packet_views import report_text
    document = {'type': 'doc', 'content': [
        {'type': 'heading', 'attrs': {'blockId': 'b1', 'level': 2}, 'content': [{'type': 'text', 'text': '经营'}]},
        {'type': 'paragraph', 'attrs': {'blockId': 'b2'}, 'content': [
            {'type': 'text', 'text': '收入 12 百万美元。'}, {'type': 'citation', 'attrs': {'sourceId': 'src_a'}}]},
        {'type': 'table', 'attrs': {'blockId': 'b3'}, 'content': [{'type': 'tableRow', 'content': [
            {'type': 'tableHeader', 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': '指标'}]}]},
            {'type': 'tableCell', 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': '12'}]}]}]}]}]}
    assert report_text(document) == '[b1] ## 经营\n[b2] 收入 12 百万美元。[src_a]\n[b3] 表格：\n  | 指标 | 12 |\n'

    store = Store(tmp_path)
    source = store.add_source('Synthetic', 'Revenue was USD 12 million.')
    run = store.create_run({'title': 'T', 'objective': 'Explain revenue'}, [source['id']])
    brief = store.publish(run['id'], {'title': 'T', 'markdown': 'Revenue was USD 12 million.'})
    _, files = build_packet(store, brief['id'], store.root / 'review')
    packet = store.root / 'review/packet'
    for name in ('report.txt', 'requirements.json', 'claims.json', 'numbers.json', 'citations.json', 'overview.json'):
        assert name in files
    assert 'Revenue was USD 12 million.' in (packet / 'report.txt').read_text(encoding='utf-8')
    overview = json.loads((packet / 'overview.json').read_text(encoding='utf-8'))
    listed = {item['path']: item for item in overview['files']}
    assert listed['report.txt']['contains'] and listed['report.txt']['bytes'] > 0
    assert 'document' in overview['target_json_sections_chars']
    assert json.loads((packet / 'requirements.json').read_text(encoding='utf-8'))['requirements']['requirement_items']
