test('subscription.list and subscription.cancel via /admin/subscriptions', async () => {
  const res = await cancelSubscription('s-1', {idempotencyKey: 'k-1'});
  expect(res.state).toBe('cancelled');
  await expect(cancelSubscription('s-1', {idempotencyKey: 'k-1'})).resolves.toEqual(res);
});
