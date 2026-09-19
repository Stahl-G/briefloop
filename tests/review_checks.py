"""Synthetic Reviewer outputs that claim status=complete must cover every
requirement item, as admission now demands."""


def requirement_checks(target):
    return [{'requirement_id': item['requirement_id'],
             'status': 'manual' if item.get('mode') == 'manual' else 'covered',
             'reason': 'Synthetic check'}
            for item in target['requirements']['requirement_items']]


def for_version(store, version_id):
    from briefloop.review import _snapshot
    return requirement_checks(_snapshot(store, version_id))
