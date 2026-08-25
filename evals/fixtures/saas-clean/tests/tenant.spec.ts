test('tenant.list and tenant.suspend via /admin/tenants', async () => {
  const res = await suspendTenant('t-1', {reason: 'abuse'});
  expect(res.status).toBe('suspended');
  expect(auditFor('tenant.suspend', 't-1')).toHaveLength(1);
});
test('/admin/tenants forbids out-of-scope operators', async () => {
  await expect(suspendTenant('t-9', {as: 'support'})).rejects.toThrow('forbidden');
});
