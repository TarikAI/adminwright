test('role matrix', () => {
  expect(can('dispatcher', 'job.reassign', {region: 'north'})).toBe(true);
});
