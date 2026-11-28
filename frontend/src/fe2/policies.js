// Keep UI affordances consistent with the states and roles accepted by the API.
export function paymentControls(order, roles = []) {
  const fin = roles.includes('FIN');
  const sal = roles.includes('SAL');
  const reversed = new Set(order?.payments?.filter(payment => payment.reversal_of).map(payment => payment.reversal_of) || []);
  return {
    record: fin && order?.status !== 'CANCELLED' && (order?.balance?.balance_vnd || 0) > 0,
    changeDue: (fin || sal) && !!order && order.status !== 'CANCELLED',
    reverse: payment => fin && !!payment && !payment.reversal_of && payment.status === 'POSTED' && !reversed.has(payment.code),
  };
}

export function warehouseControls(lot, document, roles = []) {
  const whs = roles.includes('WHS');
  const mgr = roles.includes('MGR');
  const open = !!lot && !['COMPLETED', 'CANCELLED'].includes(lot.status);
  return {
    editLine: whs && open,
    addLine: whs && open,
    receipt: whs && !!lot && ['WAITING_RECEIPT', 'IN_STORAGE'].includes(lot.status) && lot.lines.length > 0,
    issue: whs && !!lot && lot.status === 'IN_STORAGE' && lot.lines.length > 0,
    close: whs && !!lot && lot.status === 'IN_STORAGE',
    editDraft: whs && document?.status === 'DRAFT' && document.doc_type !== 'ADJUSTMENT',
    adjust: whs && lot?.status === 'IN_STORAGE' && document?.status === 'POSTED' && ['RECEIPT', 'ISSUE'].includes(document.doc_type) && document.lines.some(line => line.remaining_adjustable > 0),
    decide: mgr && document?.doc_type === 'ADJUSTMENT' && document.status === 'DRAFT',
  };
}
