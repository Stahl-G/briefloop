"""Small, reproducible input table for periodic reports; source truth remains external."""
from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class IndustryMetric(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    metric: str = Field(min_length=1)
    product: str = ''
    region: str = ''
    unit: str = Field(min_length=1)
    current: float | None = None
    current_date: date | None = None
    as_of: date | None = None
    previous_as_of: date | None = None
    previous: float | None = None
    previous_date: date | None = None
    source_id: str = Field(min_length=1)
    locator: str = ''
    previous_source_id: str | None = None
    previous_locator: str = ''
    category: Literal['actual','forecast','guidance','consensus'] = 'actual'
    previous_category: Literal['actual','forecast','guidance','consensus'] | None = None
    tax_basis: str = ''
    previous_unit: str | None = None
    previous_tax_basis: str | None = None
    comparison: Literal['pct','bp','difference','none'] = 'none'
    comparable: bool = False
    notes: str = ''

class IndustryData(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['industry_periodic.v1'] = 'industry_periodic.v1'
    records: list[IndustryMetric] = Field(default_factory=list)


def prepare_report_data(payload):
    """Recalculate changes from raw inputs; never accept caller-provided derived values."""
    data=IndustryData.model_validate(payload)
    results=[]; gaps=[]
    for i,row in enumerate(data.records):
        value=None; reason=''
        if row.current is None: reason='本期值缺失'
        elif row.current_date is None: reason='缺少本期指标日期 current_date'
        elif row.category!='actual' and row.as_of is None: reason='预测、指引或一致预期缺少取得数据的截至日 as_of'
        elif row.comparison!='none':
            if row.previous is None or row.previous_date is None: reason='比较值或日期缺失'
            elif row.previous_date>row.current_date: reason='比较值日期晚于本期指标日期，不能按时间序列计算'
            elif not row.comparable: reason='尚未确认产品、地区、期间及口径可比'
            elif row.previous_unit is None or row.previous_tax_basis is None or row.previous_category is None: reason='缺少比较值单位、税口径或数据类别'
            elif row.previous_unit!=row.unit or row.previous_tax_basis!=row.tax_basis or row.previous_category!=row.category: reason='比较口径不一致'
            elif row.comparison=='pct' and row.previous==0: reason='比较值为零，不能计算百分比'
            elif row.comparison=='bp' and row.unit not in ('%','percent'): reason='基点变化要求输入为百分数（例如 4.5 表示 4.5%）'
            else:
                cur=Decimal(str(row.current));prev=Decimal(str(row.previous))
                value=float((cur-prev)/abs(prev)*100 if row.comparison=='pct' else (cur-prev)*100 if row.comparison=='bp' else cur-prev)
        if reason:gaps.append(f'{row.metric}：{reason}')
        results.append({'record_index':i,'change':value,'change_unit':'%' if row.comparison=='pct' else 'bp' if row.comparison=='bp' else row.unit,'gap':reason})
    def cell(s):return str(s if s is not None else '未提供').replace('|','\\|').replace('\n',' ')
    category_names={'actual':'实际','forecast':'预测','guidance':'公司指引','consensus':'一致预期'}
    lines=['| 指标 / 产品 / 地区 | 本期值 | 指标日期 / 数据截至日 | 比较值 / 日期 / 数据截至日 | 变化 | 类别 / 税口径 | 来源 |', '| --- | --- | --- | --- | --- | --- | --- |']
    for row,result in zip(data.records,results):
        change=result['gap'] or ('—' if result['change'] is None else f"{result['change']:+.2f} {result['change_unit']}")
        label=' / '.join(v for v in (row.metric,row.product,row.region) if v)
        values=[label,f'{row.current:g} {row.unit}' if row.current is not None else '未提供',f'{row.current_date or "未注明"} / {row.as_of or "未注明"}',
                f'{row.previous:g} {row.previous_unit or row.unit} / {row.previous_date} / {row.previous_as_of or "未注明"}' if row.previous is not None else '未提供',change,f'{category_names[row.category]} / {row.tax_basis or "未注明"}',f'[@{row.source_id}]'+(f' [@{row.previous_source_id}]' if row.previous_source_id else '')]
        lines.append('| '+' | '.join(cell(v) for v in values)+' |')
    return {**data.model_dump(mode='json'),'calculations':results,'gaps':gaps,'markdown':'\n'.join(lines)}


def report_data_template():
    """An empty valid template, with schema alongside it rather than fabricated market facts."""
    return IndustryData().model_dump(mode='json')
