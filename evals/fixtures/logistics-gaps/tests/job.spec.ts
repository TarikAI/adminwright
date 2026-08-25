test('job.list via /admin/jobs', async () => {
  expect((await listJobs({region: 'north'})).length).toBeGreaterThan(0);
});
