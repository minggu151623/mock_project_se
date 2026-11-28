import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, human, money } from '../api';
import { ApiError } from './OperationForm';
import { apiTime, displayTime, localTime, pageOffset, requestKey, validDay } from './operations';
import './operations.css';
import { paymentControls } from './policies.js';

const statusName = { UNPAID: 'Chưa thu', PARTIALLY_PAID: 'Thu một phần', PAID: 'Đã thu đủ', NOT_APPLICABLE: 'Không áp dụng', POSTED: 'Đã ghi nhận', REVERSED: 'Đã đảo', CASH: 'Tiền mặt', BANK_TRANSFER: 'Chuyển khoản' };
const name = value => statusName[value] || human(value);
const dateText = value => /^\d{4}-\d{2}-\d{2}$/.test(value || '') ? `${value.slice(5, 7)}/${value.slice(8, 10)}/${value.slice(0, 4)}` : '—';

function useLoad(path, revision) {
  const [state, setState] = useState({ loading: true });
  useEffect(() => {
    if (!path) { setState({ loading: false }); return; }
    let live = true; setState({ loading: true });
    api(path).then(data => { if (live) setState({ data, loading: false }); }).catch(error => { if (live) setState({ error, loading: false }); });
    return () => { live = false; };
  }, [path, revision]);
  return state;
}

function PaymentForm({ order, payment, close, saved }) {
  const reversing = !!payment;
  const [amount, setAmount] = useState(''), [method, setMethod] = useState('CASH');
  const [reference, setReference] = useState(''), [note, setNote] = useState('');
  const [occurredAt, setOccurredAt] = useState(localTime(new Date())), [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState(null), [uncertain, setUncertain] = useState(false);
  const attempt = useRef(null), lock = useRef(false);
  const submit = async event => {
    event.preventDefault(); if (lock.current) return;
    const body = reversing ? { reason: reason.trim() } : { amount_vnd: Number(amount), occurred_at: apiTime(occurredAt), method, reference: method === 'BANK_TRANSFER' ? reference.trim() : null, note: note.trim() };
    if (!reversing && (!Number.isSafeInteger(body.amount_vnd) || body.amount_vnd <= 0 || body.amount_vnd > order.balance.balance_vnd)) { setError(new Error(`Số tiền cần là số nguyên dương, tối đa ${money(order.balance.balance_vnd)}.`)); return; }
    if (!reversing && new Date(body.occurred_at).getTime() > Date.now()) { setError(new Error('Thời điểm thực tế không được ở tương lai.')); return; }
    const path = reversing ? `/sales/payments/${payment.code}/reverse` : `/sales/orders/${order.code}/payments`;
    attempt.current = requestKey(attempt.current, path, 'POST', body);
    lock.current = true; setBusy(true); setError(null);
    try { await api(path, { method: 'POST', body, headers: { 'Idempotency-Key': attempt.current.key }, signal: AbortSignal.timeout(20000) }); saved(); }
    catch (e) { setError(e); setUncertain(!e.status || e.status >= 500); }
    finally { lock.current = false; setBusy(false); }
  };
  return <div className="modal-backdrop"><div className="modal-card ops-modal" role="dialog" aria-modal="true" aria-label={reversing ? 'Đảo khoản thu' : 'Ghi nhận thu tiền'}><h2>{reversing ? `Đảo khoản thu ${payment.code}` : `Ghi nhận thu cho ${order.code}`}</h2>
    {reversing ? <p>Đảo toàn bộ {money(payment.amount_vnd)}. Khoản gốc và bản đảo đều được giữ trong lịch sử; đây không phải lệnh hoàn tiền tại ngân hàng.</p> : <p>Còn phải thu: <strong>{money(order.balance.balance_vnd)}</strong>. Đây là ghi nhận thủ công sau đối chiếu, không xác nhận tiền từ ngân hàng.</p>}
    <form onSubmit={submit}><fieldset disabled={busy || uncertain}>
      {reversing ? <label>Lý do đảo *<textarea required maxLength="2000" value={reason} onChange={e => setReason(e.target.value)}/></label> : <>
        <label>Số tiền thu (VND) *<input required type="number" min="1" max={order.balance.balance_vnd} step="1" value={amount} onChange={e => setAmount(e.target.value)}/></label>
        <label>Thời điểm thực tế (giờ Việt Nam) *<input required type="datetime-local" step="1" value={occurredAt} onChange={e => setOccurredAt(e.target.value)}/></label>
        <label>Phương thức *<select value={method} onChange={e => setMethod(e.target.value)}><option value="CASH">Tiền mặt</option><option value="BANK_TRANSFER">Chuyển khoản</option></select></label>
        {method === 'BANK_TRANSFER' && <label>Tham chiếu chứng từ chuyển khoản *<input required maxLength="200" value={reference} onChange={e => setReference(e.target.value)}/></label>}
        <label>Ghi chú<textarea maxLength="2000" value={note} onChange={e => setNote(e.target.value)}/></label>
      </>}
    </fieldset><ApiError error={error}/>{error?.details?.existing_payment_code && <p role="alert">Khoản đã có: {error.details.existing_payment_code}. Kiểm tra lịch sử trước khi thử lại.</p>}
    {uncertain && <p role="alert">Chưa rõ máy chủ đã ghi nhận hay chưa. Thử lại sẽ dùng cùng khóa xác nhận và nội dung.</p>}
    <div className="modal-actions"><button type="button" className="btn btn-outline" disabled={busy} onClick={close}>Đóng</button><button className="btn btn-primary" disabled={busy}>{busy ? 'Đang xử lý…' : uncertain ? 'Thử lại cùng yêu cầu' : 'Xác nhận'}</button></div></form></div></div>;
}

function DueDateForm({ order, close, saved }) {
  const [date, setDate] = useState(order.payment_due_date || ''), [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState(null), [uncertain, setUncertain] = useState(false);
  const submit = async event => { event.preventDefault(); setBusy(true); setError(null);
    try { await api(`/sales/orders/${order.code}/due-date`, { method: 'PATCH', body: { version: order.version, due_date: date, reason: reason.trim() }, signal: AbortSignal.timeout(20000) }); saved(); }
    catch (e) { setError(e); if (e.code === 'VERSION_CONFLICT') saved('Đơn đã được sửa bởi người khác. Đã tải lại; hãy kiểm tra ngày trước khi sửa tiếp.'); else setUncertain(!e.status || e.status >= 500); }
    finally { setBusy(false); }
  };
  return <div className="modal-backdrop"><div className="modal-card ops-modal" role="dialog" aria-modal="true" aria-label="Đổi ngày đến hạn"><h2>Đổi ngày đến hạn</h2><form onSubmit={submit}><label>Ngày đến hạn *<input required type="date" value={date} onChange={e => setDate(e.target.value)}/></label><label>Lý do *<textarea required value={reason} onChange={e => setReason(e.target.value)}/></label><ApiError error={error}/>{uncertain && <p role="alert">Chưa rõ cập nhật đã thành công. Đóng và kiểm tra lại đơn trước khi sửa tiếp.</p>}<div className="modal-actions"><button type="button" className="btn btn-outline" onClick={close}>Đóng</button><button className="btn btn-primary" disabled={busy || uncertain}>Lưu</button></div></form></div></div>;
}

export default function PaymentsPage({ me }) {
  const [params, setParams] = useSearchParams(); const code = params.get('order'); const [revision, setRevision] = useState(0);
  const [search, setSearch] = useState(params.get('q') || ''), [modal, setModal] = useState(null), [notice, setNotice] = useState('');
  const offset = pageOffset(params.get('offset')), limit = 20; const query = new URLSearchParams({ limit, offset });
  for (const key of ['q','customer_code','service_type','payment_status','due_from','due_to']) if (params.get(key)) query.set(key, params.get(key));
  if (params.get('overdue_only') === 'true') query.set('overdue_only','true');
  const list = useLoad(code ? null : `/sales/orders?${query}`, revision);
  const detail = useLoad(code ? `/sales/orders/${encodeURIComponent(code)}` : null, revision);
  const update = patch => { const next = new URLSearchParams(params); Object.entries(patch).forEach(([k, v]) => v == null || v === '' ? next.delete(k) : next.set(k, String(v))); setParams(next); };
  const saved = (message = 'Đã lưu thành công.') => { setModal(null); setRevision(n => n + 1); setNotice(message); };
  const fin = me.roles?.includes('FIN'), sal = me.roles?.includes('SAL'); const order = detail.data, balance = order?.balance;
  const controls = paymentControls(order, me.roles);
  return <div className="ops-page"><div className="page-heading"><div><span className="eyebrow">TÀI CHÍNH</span><h1>Thanh toán và công nợ</h1><p>Theo dõi khoản phải thu và lịch sử ghi nhận thủ công.</p></div><button className="btn btn-outline" onClick={() => setRevision(n => n + 1)}>Làm mới</button></div>
    {notice && <p role="status" className="ops-notice">{notice}</p>}{!code ? <section className="panel"><form className="ops-filters" onSubmit={e => { e.preventDefault(); update({ q: search, offset: null }); }}><label>Mã đơn<input value={search} onChange={e => setSearch(e.target.value)}/></label><button className="btn btn-outline">Tìm</button><label>Khách hàng<input value={params.get('customer_code') || ''} onChange={e => update({ customer_code: e.target.value, offset: null })}/></label><label>Dịch vụ<select value={params.get('service_type') || ''} onChange={e => update({ service_type: e.target.value, offset: null })}><option value="">Tất cả</option>{['TRANSPORT','RENTAL','WAREHOUSE'].map(s => <option key={s} value={s}>{human(s)}</option>)}</select></label><label>Thanh toán<select value={params.get('payment_status') || ''} onChange={e => update({ payment_status: e.target.value, offset: null })}><option value="">Tất cả</option>{['UNPAID','PARTIALLY_PAID','PAID'].map(s => <option key={s} value={s}>{name(s)}</option>)}</select></label><label>Đến hạn từ<input type="date" value={validDay(params.get('due_from'), '')} onChange={e => update({ due_from: e.target.value, offset: null })}/></label><label>Đến hạn tới<input type="date" value={validDay(params.get('due_to'), '')} onChange={e => update({ due_to: e.target.value, offset: null })}/></label><label className="ops-check"><input type="checkbox" checked={params.get('overdue_only') === 'true'} onChange={e => update({ overdue_only: e.target.checked ? 'true' : null, offset: null })}/>Chỉ quá hạn</label></form>
      <ApiError error={list.error}/>{list.loading ? <p role="status">Đang tải…</p> : list.data && <><div className="ops-facts"><div><strong>Phải thu</strong><p>{money(list.data.summary.amount_due_vnd)}</p></div><div><strong>Đã thu</strong><p>{money(list.data.summary.amount_paid_vnd)}</p></div><div><strong>Còn lại</strong><p>{money(list.data.summary.balance_vnd)}</p></div></div><p>Tổng theo bộ lọc trên tất cả các trang; đơn đã hủy có nghĩa vụ thu bằng 0.</p><div className="table-wrap"><table><thead><tr><th>Đơn / khách</th><th>Dịch vụ</th><th>Trạng thái</th><th>Đến hạn</th><th>Phải thu</th><th>Đã thu</th><th>Còn lại</th><th></th></tr></thead><tbody>{list.data.items.map(item => <tr key={item.code}><td>{item.code}<br/>{item.customer_code}</td><td>{human(item.service_type)}</td><td>{human(item.status)}<br/>{name(item.balance.payment_status)}{item.overdue && <strong className="ops-late">Quá hạn</strong>}</td><td>{dateText(item.payment_due_date)}</td><td>{money(item.balance.amount_due_vnd)}</td><td>{money(item.balance.amount_paid_vnd)}</td><td>{money(item.balance.balance_vnd)}</td><td><button className="btn btn-outline" onClick={() => update({ order: item.code })}>Chi tiết</button></td></tr>)}</tbody></table></div><div className="ops-pagination"><span>{list.data.total} đơn · Trang {Math.floor(offset / limit)+1}</span><button disabled={!offset} onClick={() => update({ offset: Math.max(0, offset-limit) })}>Trước</button><button disabled={offset+limit >= list.data.total} onClick={() => update({ offset: offset+limit })}>Sau</button></div></>}</section> : <><button className="btn btn-outline ops-back" onClick={() => update({ order: null })}>Về danh sách</button><ApiError error={detail.error}/>{order && <><section className="panel"><h2>{order.code} · {human(order.status)}</h2><dl className="ops-facts"><div><dt>Khách / dịch vụ</dt><dd>{order.customer_code} · {human(order.service_type)}</dd></div><div><dt>Ngày đến hạn</dt><dd>{dateText(balance.payment_due_date)}</dd></div><div><dt>Giá đã chốt</dt><dd>{money(balance.quoted_vnd)}</dd></div><div><dt>Phụ phí đã duyệt</dt><dd>{money(balance.approved_charges_vnd)}</dd></div><div><dt>Tổng phải thu</dt><dd>{money(balance.amount_due_vnd)}</dd></div><div><dt>Đã thu / còn lại</dt><dd>{money(balance.amount_paid_vnd)} / {money(balance.balance_vnd)}</dd></div><div><dt>Trạng thái thanh toán</dt><dd>{name(balance.payment_status)}</dd></div></dl><div className="ops-actions">{controls.record && <button className="btn btn-primary" onClick={() => setModal({ type: 'payment' })}>Ghi nhận thu tiền</button>}{controls.changeDue && <button className="btn btn-outline" onClick={() => setModal({ type: 'due' })}>Đổi ngày đến hạn</button>}</div></section>
      <section className="panel"><h2>Phụ phí và quyết định</h2>{order.charges.length === 0 && <p>Chưa có phụ phí.</p>}{order.charges.map(charge => <p key={charge.code}>{charge.code} · {money(charge.amount_vnd)} · {name(charge.status)} · {charge.reason} {charge.decision_note && `· ${charge.decision_note}`}</p>)}</section>
      <section className="panel"><h2>Lịch sử thu và đảo</h2>{order.payments.length === 0 && <p>Chưa có khoản thu.</p>}<div className="table-wrap"><table><thead><tr><th>Mã</th><th>Thời điểm</th><th>Số tiền</th><th>Phương thức / tham chiếu</th><th>Người ghi</th><th>Trạng thái / nguồn / lý do</th><th></th></tr></thead><tbody>{order.payments.map(payment => <tr key={payment.code}><td>{payment.code}</td><td>{displayTime(payment.occurred_at)}</td><td>{payment.reversal_of ? '−' : ''}{money(payment.amount_vnd)}</td><td>{name(payment.method)}<br/>{payment.reference || '—'}</td><td>{payment.created_by}<br/>{displayTime(payment.created_at)}</td><td>{name(payment.status)}{payment.reversal_of && ` · Đảo ${payment.reversal_of}`}{payment.reason && ` · ${payment.reason}`}</td><td>{controls.reverse(payment) && <button className="btn btn-outline" onClick={() => setModal({ type: 'reverse', payment })}>Đảo khoản thu</button>}</td></tr>)}</tbody></table></div></section></>}</>}
    {modal?.type === 'payment' && <PaymentForm order={order} close={() => setModal(null)} saved={saved}/ >}{modal?.type === 'reverse' && <PaymentForm order={order} payment={modal.payment} close={() => setModal(null)} saved={saved}/ >}{modal?.type === 'due' && <DueDateForm order={order} close={() => setModal(null)} saved={saved}/ >}
  </div>;
}
