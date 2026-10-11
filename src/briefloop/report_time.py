"""Host-clock report periods, frozen once at admission (never on retry)."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import calendar
import re

# An analysis "as of now" has no news window: history is usable evidence.
# A today-only window here filtered every Tavily search to one day (2026-10 Manus).
AS_OF_PERIODS = ('截至提交时刻', '截至目前', '截至今日', '截至今天', '截至当前', 'as of now', 'as of today')


def freeze(requirements, instant=None):
    clock = instant or datetime.now().astimezone()
    zone = requirements.get('report_timezone') or str(clock.tzinfo)
    try:
        local = clock.astimezone(ZoneInfo(zone)) if requirements.get('report_timezone') else clock
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError('无效报告时区，请使用 Asia/Shanghai 等 IANA 时区') from exc
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    period = requirements.get('period', '').strip()
    start_text, end_text = requirements.get('period_start'), requirements.get('period_end')
    basis = 'explicit'
    def parse(value):
        dt = datetime.fromisoformat(value)
        return dt.replace(tzinfo=local.tzinfo) if dt.tzinfo is None else dt.astimezone(local.tzinfo)
    if period.casefold() in AS_OF_PERIODS and not (start_text or end_text):
        start, end = None, local
        basis = 'as_of'
    elif start_text or end_text:
        if not start_text or not end_text:
            raise ValueError('请同时填写报告开始和结束日期')
        start, end = parse(start_text), parse(end_text)
        if len(end_text) == 10: end += timedelta(days=1)
    elif period in ('', '最新动态', '最新', '近期', 'today', '今天', '今日', '日报', '本日'):
        start, end = midnight, local
        basis = 'today_default'
    elif period in ('本周', 'this week'):
        start, end = midnight-timedelta(days=local.weekday()), local
    elif period in ('本月', 'this month'):
        start, end = midnight.replace(day=1), local
    elif period in ('昨天', '昨日'):
        start, end = midnight-timedelta(days=1), midnight
    elif re.fullmatch(r'近\s*\d+\s*(小时|天|日)', period):
        amount = int(re.search(r'\d+', period)[0])
        end = local
        start = end-timedelta(hours=amount) if '小时' in period else end-timedelta(days=amount)
    elif re.fullmatch(r'\d{4}\s*年第\s*\d{1,2}\s*周', period):
        year, week = map(int, re.findall(r'\d+', period))
        start = datetime.fromisocalendar(year, week, 1).replace(tzinfo=local.tzinfo)
        end = start+timedelta(days=7)
    elif re.fullmatch(r'\d{4}', period):
        start = midnight.replace(year=int(period), month=1, day=1)
        end = start.replace(year=start.year+1)
    else:
        dates = re.findall(r'\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)?', period)
        remainder = period
        for value in dates: remainder = remainder.replace(value, '', 1)
        if dates and len(dates) <= 2 and re.fullmatch(r'[\s至到~—–-]*', remainder):
            start, end = parse(dates[0]), parse(dates[-1])
            if len(dates[-1]) == 10: end += timedelta(days=1)
        else:
            month = None
            for fmt in ('%Y-%m', '%Y年%m月', '%B %Y', '%b %Y'):
                try: month = datetime.strptime(period, fmt); break
                except ValueError: pass
            if month is None:
                raise ValueError('报告时间范围不明确，请填写开始和结束日期；例如 2026-09-01 至 2026-09-14')
            start = month.replace(tzinfo=local.tzinfo)
            end = start+timedelta(days=calendar.monthrange(start.year, start.month)[1])
    if start is not None and start >= end:
        raise ValueError('报告结束时间必须晚于开始时间')
    window = {'checked_at': clock.isoformat(), 'today': local.date().isoformat(),
              'timezone': zone, 'start': start.isoformat() if start else None, 'end_exclusive': end.isoformat(),
              'basis': basis, 'clock_source': 'system_clock'}
    if start is None:
        window['mode'] = 'as_of'
    warnings = []
    for field in ('period', 'title') if start is not None else ():
        label = re.search(r'(?:(\d{4})\s*年\s*)?第\s*(\d{1,2})\s*周', requirements.get(field, ''))
        if not label:
            continue
        year, week = int(label[1] or start.isocalendar().year), int(label[2])
        try:
            week_start = datetime.fromisocalendar(year, week, 1).replace(tzinfo=local.tzinfo)
        except ValueError:
            warnings.append({'code': 'invalid_iso_week', 'field': field,
                             'message': f'{year} 年不存在 ISO 第 {week} 周；已保留明确填写的日期范围。'})
            continue
        week_end = week_start + timedelta(days=7)
        if start < week_start or end > week_end:
            warnings.append({'code': 'iso_week_mismatch', 'field': field,
                             'message': f'ISO {year} 年第 {week} 周为 {week_start.date()} 至 '
                                        f'{(week_end-timedelta(days=1)).date()}，与本轮日期范围不一致；'
                                        '已保留用户填写的范围，请修正周编号或说明跨周覆盖。'})
    if warnings:
        window['warnings'] = warnings
    return window


def instructions(window):
    if not window:
        return '旧任务未冻结明确时间范围：不要猜测年份或声称时效已核验；新建明确日期范围的任务。'
    if window.get('mode') == 'as_of':
        return (f"系统核对日期：{window['today']}；时区：{window['timezone']}；核对时刻：{window['checked_at']}。"
                f"本报告是截至 {window['end_exclusive']} 的分析，不设“本期”起点：此前的交易、融资、产品和数据都可作为正文事实，"
                '须写明事件日期和数据期间，区分历史与现状；不使用晚于核对时刻的信息。检索不按发布日期限定，旧报道仍须核对是否已被后续事件更正。'
                '关键事件可用 temporal_claims 记录 statement、event_date、source_id、locator 与 usage（current 为现状判断依据，background 为历史背景），不需要证明“本期新增”，正文不写“本期未发现新增”之类的检索过程。'
                '日期记录仍须回读原文；程序日期比较不等于事实认证。')
    return (f"系统核对日期：{window['today']}；时区：{window['timezone']}；核对时刻：{window['checked_at']}。"
            f"本轮冻结范围：{window['start']} 至 {window['end_exclusive']}（不含结束时刻）。"
            '所有检索、子任务、写作和评分沿用此范围，恢复任务不得移动范围。'
            '稿件应逐条提供当期动态的 temporal_claims：statement、event_date、published_at、fetched_at、source_id、locator、usage(current/background)。'
            '当期依据 news_basis 可为 event（本期发生，默认，比较 event_date）、first_disclosure（本期首次披露）或 new_development（本期新进展）。'
            '首次披露或新进展须提供 news_date、news_note（本期具体新增什么）、source_id 与 locator；保留真实 event_date，不把旧事件日期改为报道日。'
            'published_at 与 fetched_at 仅为元数据，不能自动证明首次披露或新进展；转载、重新抓取、无实质变化的页面更新不能算当期新增。'
            '缺少新闻性依据须标为待核实；旧事件无本期新增依据时作背景。正文分别写“本周发生”“本周首次披露”或“本周新进展”。'
            '日期记录和新闻性仍须独立回读原文；程序日期比较不等于事实认证。'
            + ''.join('时间范围提示：' + warning['message'] for warning in window.get('warnings', [])))


def check(window, claims):
    if not window: return {'status': 'not_checked', 'reason': 'legacy_window', 'items': []}
    warnings = window.get('warnings', [])
    if not claims: return {'status': 'not_checked', 'reason': 'missing_date_records', 'items': [], 'warnings': warnings}
    end = datetime.fromisoformat(window['end_exclusive'])
    if window.get('start') is None:
        # As-of analysis: only information after the cutoff is out of range.
        items = []
        for claim in claims:
            status, value = ('background' if claim.get('usage') == 'background' else 'as_of_unverified'), claim.get('event_date') or ''
            try:
                event = datetime.fromisoformat(value) if value else None
                if event is not None and (event.replace(tzinfo=end.tzinfo) if event.tzinfo is None else event) >= end:
                    status = 'out_of_range'
            except ValueError: pass
            items.append({**claim, 'news_basis': claim.get('news_basis', 'event'), 'basis_date': value,
                          'temporal_status': status, 'reason': 'after_cutoff' if status == 'out_of_range' else 'as_of_window'})
        return {'status': 'needs_source_review', 'items': items, 'warnings': warnings,
                'review_hint': '截至时点分析只检查是否使用了核对时刻之后的信息；日期仍须回读原文核对。',
                'missing_date_count': sum(not i['basis_date'] for i in items if i['temporal_status'] != 'background'),
                'out_of_range_count': sum(i['temporal_status'] == 'out_of_range' for i in items)}
    start = datetime.fromisoformat(window['start'])
    items = []
    for claim in claims:
        status = 'background' if claim.get('usage') == 'background' else 'unverified'
        basis = claim.get('news_basis', 'event')
        value = claim.get('event_date') if basis == 'event' else claim.get('news_date')
        reason = 'background' if status == 'background' else 'missing_or_invalid_basis_date'
        if basis in ('first_disclosure', 'new_development'):
            has_basis = all(str(claim.get(key) or '').strip() for key in ('news_note', 'source_id', 'locator'))
            if not has_basis and status != 'background':
                reason = 'missing_news_basis_evidence'
        else:
            has_basis = basis == 'event'
            if not has_basis and status != 'background':
                reason = 'unknown_news_basis'
        if status != 'background' and value and has_basis:
            try:
                event = datetime.fromisoformat(value)
                event = event.replace(tzinfo=start.tzinfo) if event.tzinfo is None else event
                # A source may publish only a date: compare overlapping calendar days.
                finish = event+timedelta(days=1) if len(value)==10 else event+timedelta(microseconds=1)
                status = 'in_range_unverified' if event < end and finish > start else 'out_of_range'
                reason = 'basis_date_in_range' if status == 'in_range_unverified' else 'basis_date_out_of_range'
            except ValueError: pass
        items.append({**claim, 'news_basis': basis, 'basis_date': value or '',
                      'temporal_status': status, 'reason': reason})
    return {'status': 'needs_source_review', 'items': items, 'warnings': warnings,
            'review_hint': '日期范围比较不证明新闻性；首次披露或新进展须回读 source_id/locator，核实 news_note 并保留真实事件日期。',
            'missing_date_count': sum(i['temporal_status']=='unverified' for i in items),
            'out_of_range_count': sum(i['temporal_status']=='out_of_range' for i in items)}
