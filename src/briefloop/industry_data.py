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

CHANGE_TYPES = {'emerged':'新出现','reversed':'反转','accelerated':'加速','stalled':'停滞或放缓',
                'incident':'事故或挫折','premium_gained':'获得溢价','premium_lost':'失去溢价','unchanged':'确认未变'}


class StateChange(BaseModel):
    """One subject's state at the start and end of the period; the slope is read from both ends."""
    model_config = ConfigDict(extra='forbid')
    subject: str = Field(min_length=1)
    dimension: str = Field(min_length=1)
    change_type: Literal['emerged','reversed','accelerated','stalled','incident','premium_gained','premium_lost','unchanged']
    start_state: str = ''
    start_date: date | None = None
    start_source_id: str | None = None
    start_locator: str = ''
    end_state: str = Field(min_length=1)
    end_date: date
    end_source_id: str = Field(min_length=1)
    end_locator: str = ''
    notes: str = ''


class IndustryData(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['industry_periodic.v1'] = 'industry_periodic.v1'
    records: list[IndustryMetric] = Field(default_factory=list)
    state_changes: list[StateChange] = Field(default_factory=list)


def source_ids(data):
    """Every evidence id a data payload relies on, metrics and state changes alike."""
    ids = [r.source_id for r in data.records] + [r.previous_source_id for r in data.records if r.previous_source_id]
    ids += [c.end_source_id for c in data.state_changes] + [c.start_source_id for c in data.state_changes if c.start_source_id]
    return list(dict.fromkeys(ids))


def _state_gap(change, window):
    if change.change_type != 'emerged' and not (change.start_state and change.start_date and change.start_source_id):
        return '缺少期初状态、日期或来源；只能记为新出现，不能写成从某状态变为另一状态'
    if change.start_date and change.start_date > change.end_date:
        return '期初日期晚于期末日期'
    if window:
        start, end = window
        if not start <= change.end_date < end:
            return '期末状态日期不在本期覆盖窗口内'
        # The start end must describe the period's opening, not a mid-period event.
        if change.start_date and change.start_date > start + (end - start) / 3:
            return '期初状态日期已过本期前三分之一，不能代表期初'
    return ''


def prepare_report_data(payload, window=None):
    """Recalculate changes from raw inputs; never accept caller-provided derived values.

    window is the frozen (start, end_exclusive) dates; state changes are checked against it.
    """
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
            elif row.category!='actual' and row.previous_as_of is None: reason='比较预测、指引或一致预期缺少取得数据的截至日 previous_as_of'
            elif row.category!='actual' and row.previous_date==row.current_date and row.previous_as_of>row.as_of: reason='同一目标期的比较值数据截至日晚于本期数据截至日，不能按先后版本计算'
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
    states=[]
    for i,change in enumerate(data.state_changes):
        reason=_state_gap(change,window)
        if reason:gaps.append(f'{change.subject}／{change.dimension}：{reason}')
        states.append({'state_index':i,'gap':reason})
    if data.state_changes:
        lines+=['','| 主体 / 维度 | 期初状态（日期） | 期末状态（日期） | 变化 | 来源 |','| --- | --- | --- | --- | --- |']
        for change,result in zip(data.state_changes,states):
            start=f'{change.start_state}（{change.start_date}）' if change.start_state else '本期前不存在或未取得'
            kind=CHANGE_TYPES[change.change_type]+(f'；未核验：{result["gap"]}' if result['gap'] else '')
            refs=f'[@{change.end_source_id}]'+(f' [@{change.start_source_id}]' if change.start_source_id and change.start_source_id!=change.end_source_id else '')
            lines.append('| '+' | '.join(cell(v) for v in (f'{change.subject} / {change.dimension}',start,f'{change.end_state}（{change.end_date}）',kind,refs))+' |')
    return {**data.model_dump(mode='json'),'calculations':results,'state_checks':states,'gaps':gaps,'markdown':'\n'.join(lines)}


def report_data_template():
    """An empty valid template, with schema alongside it rather than fabricated market facts."""
    return IndustryData().model_dump(mode='json')
