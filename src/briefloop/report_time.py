"""Host-clock report periods, frozen once at admission (never on retry)."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import calendar
import re


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
    if start_text or end_text:
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
    if start >= end:
        raise ValueError('报告结束时间必须晚于开始时间')
    return {'checked_at': clock.isoformat(), 'today': local.date().isoformat(),
            'timezone': zone, 'start': start.isoformat(), 'end_exclusive': end.isoformat(),
            'basis': basis, 'clock_source': 'system_clock'}


def instructions(window):
    if not window:
        return '旧任务未冻结明确时间范围：不要猜测年份或声称时效已核验；新建明确日期范围的任务。'
    return (f"系统核对日期：{window['today']}；时区：{window['timezone']}；核对时刻：{window['checked_at']}。"
            f"本轮冻结范围：{window['start']} 至 {window['end_exclusive']}（不含结束时刻）。"
            '所有检索、子任务、写作和评分沿用此范围，恢复任务不得移动范围。'
            '稿件应逐条提供当期动态的 temporal_claims：statement、event_date、published_at、fetched_at、source_id、locator、usage(current/background)。'
            '事件日期决定是否当期，发布日期与抓取日期分别记录，不能相互替代。'
            '范围外事件只能作背景；事件日期不明须明确标为待核实，不算当期已证实动态。'
            '日期记录还须独立核对原文；程序日期比较不等于事实认证。')


def check(window, claims):
    if not window: return {'status': 'not_checked', 'reason': 'legacy_window', 'items': []}
    if not claims: return {'status': 'not_checked', 'reason': 'missing_date_records', 'items': []}
    start = datetime.fromisoformat(window['start'])
    end = datetime.fromisoformat(window['end_exclusive'])
    items = []
    for claim in claims:
        status = 'background' if claim.get('usage') == 'background' else 'unverified'
        value = claim.get('event_date')
        if status != 'background' and value:
            try:
                event = datetime.fromisoformat(value)
                event = event.replace(tzinfo=start.tzinfo) if event.tzinfo is None else event
                # A source may publish only a date: compare overlapping calendar days.
                finish = event+timedelta(days=1) if len(value)==10 else event+timedelta(microseconds=1)
                status = 'in_range_unverified' if event < end and finish > start else 'out_of_range'
            except ValueError: pass
        items.append({**claim, 'temporal_status': status})
    return {'status': 'needs_source_review', 'items': items,
            'missing_date_count': sum(i['temporal_status']=='unverified' for i in items),
            'out_of_range_count': sum(i['temporal_status']=='out_of_range' for i in items)}
