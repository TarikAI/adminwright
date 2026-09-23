test('role matrix', () => {
  expect(can('ops', 'alpha.list', {})).toBe(true);
});
