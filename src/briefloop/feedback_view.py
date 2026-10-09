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


def for_report(store, version_id):
    run_id=store.one('briefs',version_id)['run_id']
    rows=store.rows("SELECT j.id,j.status,j.result,j.updated FROM jobs j WHERE j.kind='learn' AND EXISTS("
                    "SELECT 1 FROM feedback f JOIN briefs b ON b.id=f.version_id WHERE f.batch_id=j.id AND b.run_id=?) "
                    "ORDER BY j.rowid DESC LIMIT 1",(run_id,))
    return rows[0] if rows else None


def pending_for_report(store,version_id):
    run_id=store.one('briefs',version_id)['run_id']
    return store.rows("SELECT count(*) AS n FROM feedback f JOIN briefs b ON b.id=f.version_id WHERE b.run_id=? AND f.batch_id IS NULL",(run_id,))[0]['n']
