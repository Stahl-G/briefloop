"""Bounded feedback history with persisted job status and workspace-wide counts."""


def snapshot(store):
    # batch_id is the learning job's primary key, not a position in recent jobs.
    # At most 100 indexed joins; never scan/parse job payloads per feedback item.
    items = store.rows("SELECT f.*, j.status AS learning_status FROM feedback f "
                       "LEFT JOIN jobs j ON j.id=f.batch_id AND j.kind='learn' "
                       "ORDER BY f.rowid DESC LIMIT 100")
    counts = store.rows("SELECT count(*) AS total, "
                        "count(CASE WHEN batch_id IS NULL THEN 1 END) AS pending FROM feedback")[0]
    return {'feedback': items, 'feedback_summary': counts}
