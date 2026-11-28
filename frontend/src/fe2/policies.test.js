import test from 'node:test';
import assert from 'node:assert/strict';
import { paymentControls, warehouseControls } from './policies.js';

test('payment controls follow role, balance and reversal state', () => {
  const order = { status: 'COMPLETED', balance: { balance_vnd: 100 }, payments: [{ code: 'PAY-1', status: 'REVERSED' }, { code: 'REV-1', reversal_of: 'PAY-1' }] };
  assert.equal(paymentControls(order, ['FIN']).record, true);
  assert.equal(paymentControls(order, ['SAL']).record, false);
  assert.equal(paymentControls(order, ['SAL']).changeDue, true);
  assert.equal(paymentControls(order, ['FIN']).reverse(order.payments[0]), false);
  assert.equal(paymentControls({ ...order, balance: { balance_vnd: 0 } }, ['FIN']).record, false);
  assert.equal(paymentControls({ ...order, status: 'CANCELLED' }, ['FIN']).record, false);
  assert.equal(paymentControls(order, ['MGR']).changeDue, false);
});

test('warehouse controls do not offer writes on a closed lot or to a reader', () => {
  const lot = { status: 'COMPLETED', lines: [{ id: 1 }] };
  const doc = { doc_type: 'ISSUE', status: 'POSTED', lines: [{ remaining_adjustable: 1 }] };
  const closed = warehouseControls(lot, doc, ['WHS']);
  assert.equal(closed.addLine, false);
  assert.equal(closed.receipt, false);
  assert.equal(closed.issue, false);
  assert.equal(closed.close, false);
  assert.equal(closed.adjust, false);
  const reader = warehouseControls({ ...lot, status: 'IN_STORAGE' }, doc, ['DSP']);
  assert.equal(reader.issue, false);
  assert.equal(reader.adjust, false);
});

test('warehouse adjustment waits for MGR while WHS may create a request', () => {
  const lot = { status: 'IN_STORAGE', lines: [{ id: 1 }] };
  const posted = { doc_type: 'RECEIPT', status: 'POSTED', lines: [{ remaining_adjustable: 1 }] };
  assert.equal(warehouseControls(lot, posted, ['WHS']).adjust, true);
  const pending = { doc_type: 'ADJUSTMENT', status: 'DRAFT', lines: [{ remaining_adjustable: 0 }] };
  assert.equal(warehouseControls(lot, pending, ['WHS']).decide, false);
  assert.equal(warehouseControls(lot, pending, ['MGR']).decide, true);
  assert.equal(warehouseControls(lot, { ...pending, status: 'POSTED' }, ['MGR']).decide, false);
});
