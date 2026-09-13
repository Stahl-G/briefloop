// Assessment activity belongs to the exact saved version, never its whole run.
export function reviewPending(version, jobs = []) {
 if (!version) return false;
 return jobs.some(job => {
  if (!['review', 'assess'].includes(job.kind) || !['queued', 'running'].includes(job.status)) return false;
  let payload = job.payload;
  if (typeof payload === 'string') { try { payload = JSON.parse(payload); } catch { return false; } }
  return payload?.version_id === version.id;
 });
}

// Pass the report's scoped jobs, not the workspace history. A retry supersedes
// its predecessor even after it finishes; the history objects remain untouched.
export function withoutSupersededRetries(jobs = []) {
 const byId = new Map(jobs.map(job => [job.id, job]));
 const superseded = new Set();
 for (const job of jobs) {
  let payload = job.payload;
  if (typeof payload === 'string') { try { payload = JSON.parse(payload); } catch { continue; } }
  const previous = byId.get(payload?.retry_of_job_id);
  if (previous && previous.id !== job.id && previous.kind === job.kind) superseded.add(previous.id);
 }
 return jobs.filter(job => !superseded.has(job.id));
}
