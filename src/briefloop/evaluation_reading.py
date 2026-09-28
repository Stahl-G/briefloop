"""Describe actual evaluation inputs without deciding whether claims are true."""
import json
import re


GUIDE = '''先分清读到的是哪一种材料。brief.markdown 中 [@src_…]、report.txt 中 [src_…]/块 ID 是内部引用和定位标记；reader-preview.md 才是本稿由产品阅读渲染器生成的短编号与来源表预览。引用展示或“没有来源表”的发现先对照该预览，不能要求作者手写编号替换绑定或重复添加来源表；预览不代表已验收 Word 分页、样式或原生点击。
机器绑定缺失、单位不支持、定位失败表示程序未检查对应项目，不等于正文事实已错。仍可回读已登记原文核查数值与含义；将未检范围和具体事实错误分开。缺口记录为空本身也不证明覆盖完整或不足。
“材料未披露/未取得/尚未找到支持”是证据范围，不是相反事实成立的证据；例如融资未披露客户付费，不能断言客户没有付费。缺证问题说明缺什么、影响哪项判断；只有原文给出相反事实才称矛盾。遇到未取得的重要原文交回研究环节，不自行联网或扩大权限。
每条 finding 锚定本版真正有问题的连续原句/块及支持判断的原文，不把旁边正确句作为错误锚点；对缺失项用已有 requirement/条款与应回答的问题说明，不伪造正文引句。同一错误散布摘要、表格和正文时合为一个发现，列出关联位置并要求一起修；不要把重复、复合发现数当独立检出数。不同事实错误仍分别记录。修订时允许反驳旧误报，保留正确事实、引用和采用条件。
核查抓取过程、临时行号、工具报错与补查路径属于研究记录；正文只保留会改变读者判断的限制，例如仅为公测、指定分支、最高比例、仍在讨论或缺少支持核心结论的数据。不能为了整洁删掉这些实质条件，也不要强迫把内部审计清单写回报告。'''


def reading_context(brief):
    detail = brief.get('detail') or {}
    if isinstance(detail, str):
        detail = json.loads(detail)
    # These counts describe saved metadata, not correctness or coverage.
    markers = [value.replace('\\', '') for value in re.findall(
        r'\\?\[@(src\\?_[a-zA-Z0-9]+)\\?\]', brief.get('markdown') or '')]
    return {
        'version_id': brief.get('id'), 'brief_hash': brief.get('hash'),
        'reader_preview_file': 'reader-preview.md',
        'preview_scope': '由产品阅读渲染器生成的同稿预览；未验证原生 Word/PDF 的分页或点击。',
        'storage_citations': {'marker_count': len(markers), 'source_ids': list(dict.fromkeys(markers)),
                              'scope': '只识别正文中保存的引用标记；不证明链接有效或原文支持。'},
        'machine_records': {'number_binding_count': len(detail.get('number_bindings') or []),
                            'temporal_record_count': len(detail.get('temporal_claims') or []),
                            'scope': '仅表示登记数量；零条不是事实错误，有记录也不是语义已核实。'},
    }
