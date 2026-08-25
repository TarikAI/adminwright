test('role x action x scope matrix', () => {
  expect(can('support', 'tenant.suspend', {tenantId: 't-1'})).toBe(false);
  expect(can('ops', 'tenant.suspend', {tenantId: 't-1'})).toBe(true);
});
