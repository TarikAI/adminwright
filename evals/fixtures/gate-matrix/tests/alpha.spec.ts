test('alpha.list via /admin/alpha', async () => {
  expect((await listAlpha()).length).toBeGreaterThan(0);
});
