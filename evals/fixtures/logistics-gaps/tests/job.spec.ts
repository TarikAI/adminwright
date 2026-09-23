test('job.list via /admin/jobs', async () => {
  expect((await listJobs({region: 'north'})).length).toBeGreaterThan(0);
});
test('job.dispatch moves a queued job to dispatched', async () => {
  expect((await dispatchJob('j-1')).status).toBe('dispatched');
});
