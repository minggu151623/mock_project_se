import test from 'node:test';
import assert from 'node:assert/strict';
import { actionConfig, actionsFor, apiTime, buildBody, displayTime, localTime, pageOffset, requestKey, tripStates, rentalStates, validDay } from './operations.js';

test('read-only roles never receive write actions in any state', () => {
  for (const role of ['SAL', 'MGR', 'ADM', 'FIN', 'WHS']) {
    for (const status of tripStates) assert.deepEqual(actionsFor('trips', { status }, [role]), []);
    for (const status of rentalStates) assert.deepEqual(actionsFor('rentals', { status, handover: {} }, [role]), []);
  }
});
test('transport does not allow skipping milestones or changing assignment after pickup', () => {
  assert.deepEqual(actionsFor('trips', { status: 'UNASSIGNED' }, ['DSP']), ['assign', 'incident']);
  assert.deepEqual(actionsFor('trips', { status: 'PICKED_UP' }, ['DSP']), ['transit', 'extend', 'incident']);
  assert.deepEqual(actionsFor('trips', { status: 'IN_TRANSIT' }, ['DSP']), ['delivery', 'extend', 'incident']);
  for (const status of ['COMPLETED', 'CANCELLED']) assert.deepEqual(actionsFor('trips', { status }, ['DSP']), []);
});
test('rental inspections are immutable and only notes remain after completion', () => {
  assert.deepEqual(actionsFor('rentals', { status: 'RESERVED' }, ['DSP']), ['change', 'release', 'handover']);
  assert.deepEqual(actionsFor('rentals', { status: 'HANDED_OVER', handover: {} }, ['DSP']), ['return', 'note']);
  assert.deepEqual(actionsFor('rentals', { status: 'COMPLETED', handover: {} }, ['DSP']), ['note']);
});
test('changed assignments use record version and numeric resource IDs', () => {
  const cfg = actionConfig('rentals', 'change', { code: 'RNT-1', version: 7 });
  assert.deepEqual(buildBody(cfg, { planned_start: '2026-12-01T08:00', planned_end: '2026-12-01T12:00', vehicle_id: '2', reason: ' đổi xe ', customer_note: 'Đã thống nhất' }), {
    planned_start: '2026-12-01T08:00:00+07:00', planned_end: '2026-12-01T12:00:00+07:00', vehicle_id: 2, reason: 'đổi xe', customer_note: 'Đã thống nhất', version: 7,
  });
});
test('confirmation payload uses attachment codes and omits unsupported version', () => {
  const cfg = actionConfig('trips', 'delivery', { code: 'TRP-1', version: 4 });
  const body = buildBody(cfg, { actual_at: '2026-12-01T12:00', result: 'SUCCESS', receiver_name: 'Khách' }, [{ code: 'ATT-1' }]);
  assert.equal(body.version, undefined); assert.deepEqual(body.attachment_codes, ['ATT-1']); assert.equal(body.note, null);
});
test('return kilometre minimum is the handover reading', () => {
  const cfg = actionConfig('rentals', 'return', { code: 'RNT-1', handover: { odometer_km: 900 } });
  assert.equal(cfg.fields.find(f => f.name === 'odometer_km').min, 900);
});
test('request retry preserves key; changed payload or endpoint receives a new key', () => {
  let i = 0; const uuid = () => `key-${++i}`;
  const first = requestKey(null, '/trips/A/pickup', 'POST', { actual_at: 'x' }, uuid);
  assert.equal(requestKey(first, '/trips/A/pickup', 'POST', { actual_at: 'x' }, uuid), first);
  assert.notEqual(requestKey(first, '/trips/A/pickup', 'POST', { actual_at: 'y' }, uuid).key, first.key);
  assert.notEqual(requestKey(first, '/trips/B/pickup', 'POST', { actual_at: 'x' }, uuid).key, first.key);
});
test('Vietnam datetime conversion is independent of the machine timezone', () => {
  assert.equal(localTime('2026-11-30T18:30:00Z'), '2026-12-01T01:30:00');
  assert.equal(new Date(apiTime('2026-12-01T01:30')).toISOString(), '2026-11-30T18:30:00.000Z');
});

test('actual time retains seconds so a subsequent milestone never rounds backwards', () => {
  assert.equal(localTime('2026-11-30T18:30:59Z'), '2026-12-01T01:30:59');
  assert.equal(apiTime('2026-12-01T01:30:59'), '2026-12-01T01:30:59+07:00');
});
test('shown time uses month/day/year and omits seconds', () => {
  assert.equal(displayTime('2026-11-29T09:00:59Z'), '16:00 11/29/2026');
});
test('invalid URL filters cannot crash schedule rendering or produce fractional offsets', () => {
  for (const value of ['bad', '2026-02-30', '2026-99-99', '']) assert.equal(validDay(value, '2026-11-27'), '2026-11-27');
  assert.equal(validDay('2026-12-01', '2026-11-27'), '2026-12-01');
  for (const value of ['bad', '-20', '1.5', 'Infinity']) assert.equal(pageOffset(value), 0);
  assert.equal(pageOffset('40'), 40);
});
