"""Identity and usage from the owned, public Codex event journal only."""


def thread_usage(events):
    threads = {}
    unattributed = 0

    def observe(thread_id, kind):
        if not isinstance(thread_id, str) or not thread_id:
            return None
        return threads.setdefault(thread_id, {'threadId': thread_id, 'kind': kind,
            'parentThreadId': None, 'agentRole': None, 'turnId': None, 'tokenUsage': None})

    for event in events:
        kind = event.get('type', '')
        data = event.get('data') or {}
        if not isinstance(data, dict):
            continue
        child = kind.startswith('child.')
        record = observe(data.get('threadId'), 'child' if child else 'main')
        if kind in ('thread.started', 'child.thread.started') and record is not None:
            for key in ('parentThreadId', 'agentRole'):
                if isinstance(data.get(key), str):
                    record[key] = data[key]
        item = event.get('item') or {}
        if isinstance(item, dict) and item.get('type') == 'collab_tool_call':
            for receiver in item.get('receiverThreadIds', []):
                observe(receiver, 'child')  # Legacy spawn order; role/parent unknown.
        elif isinstance(item, dict) and item.get('type') == 'subagent_activity':
            observe(item.get('agentThreadId'), 'child')  # Activity does not establish parent/role/usage.
        if kind not in ('thread.tokenUsage.updated', 'child.thread.tokenUsage.updated'):
            continue
        usage = data.get('tokenUsage')
        if record is None:
            unattributed += 1
            continue
        if not isinstance(usage, dict):
            continue
        # Numeric public counters only. Do not sum repeated cumulative updates,
        # infer unreported children, or assume parent and child billing disjoint.
        allowed = ('inputTokens', 'cachedInputTokens', 'cacheWriteInputTokens',
                   'outputTokens', 'reasoningOutputTokens', 'totalTokens')
        public = {}
        for period in ('last', 'total'):
            counters = usage.get(period)
            if isinstance(counters, dict):
                public[period] = {key: counters[key] for key in allowed
                    if type(counters.get(key)) is int and counters[key] >= 0}
        if type(usage.get('modelContextWindow')) is int:
            public['modelContextWindow'] = usage['modelContextWindow']
        record['tokenUsage'] = public
        record['turnId'] = data.get('turnId') if isinstance(data.get('turnId'), str) else None

    values = list(threads.values())
    missing = [value['threadId'] for value in values if value['tokenUsage'] is None]
    return {'scope': 'latest_observed_cumulative_per_thread', 'aggregation': 'none',
            'coverage': {'completeness': 'unknown', 'threads_observed': len(values),
                         'threads_with_usage': len(values) - len(missing),
                         'threads_without_usage': missing, 'unattributed_usage_events': unattributed,
                         'note': 'Only owned threads present in the public log are covered; unreported threads or usage may be missing. Parent and child counters are not added or treated as a bill.'},
            'threads': values}
