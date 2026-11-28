import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, money } from '../api';
import OperationForm, { ApiError } from './OperationForm';
import { actionConfig, actionsFor, apiTime, displayTime, field, label, localTime, pageOffset, rentalStates, tripStates, validDay } from './operations';
import './operations.css';

function useResource(path, revision = 0) {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  useEffect(() => {
    if (!path) { setState({ loading: false, data: null, error: null }); return; }
    const controller = new AbortController();
    setState({ loading: true, data: null, error: null });
    api(path, { signal: controller.signal }).then(data => { if (!controller.signal.aborted) setState({ data, loading: false, error: null }); })
      .catch(error => { if (!controller.signal.aborted) setState({ data: null, loading: false, error }); });
    return () => controller.abort();
  }, [path, revision]);
  return state;
}
function Status({ value }) { return <span className={`status-pill ${String(value).toLowerCase()}`}>{label(value)}</span>; }
function Facts({ items }) { return <dl className="ops-facts">{items.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value ?? '—'}</dd></div>)}</dl>; }
function Evidence({ files = [] }) {
  const [error, setError] = useState(null), [busy, setBusy] = useState(false);
  const download = async file => { setError(null); setBusy(true); try { const blob = await api(`/attachments/${file.code}`); const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = file.original_name; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 30000); } catch (e) { setError(e); } finally { setBusy(false); } };
  return <><div className="ops-actions">{files.map(file => <button disabled={busy} className="btn btn-outline" key={file.code} onClick={() => download(file)}>Tải {file.original_name}</button>)}</div><ApiError error={error}/></>;
}

export default function OperationsPage({ rental = false, me }) {
  const [params, setParams] = useSearchParams();
  const tab = rental ? 'rentals' : (['vehicles', 'drivers', 'schedule'].includes(params.get('tab')) ? params.get('tab') : 'trips');
  const code = params.get('record');
  const [revision, setRevision] = useState(0), [message, setMessage] = useState('');
  const update = changes => setParams(old => { const next = new URLSearchParams(old); for (const [k, v] of Object.entries(changes)) v === null || v === '' ? next.delete(k) : next.set(k, String(v)); return next; });
  const refresh = () => setRevision(x => x + 1);
  return <div className="fe2-page ops-page">
    <div className="page-heading"><div><span className="eyebrow">VẬN HÀNH</span><h1>{rental ? 'Thuê xe' : 'Vận tải'}</h1><p>{rental ? 'Giữ lịch, bàn giao và theo dõi trả xe.' : 'Điều phối nguồn lực và theo dõi từng chặng giao nhận.'} Thời gian hiển thị theo Việt Nam.</p></div><button className="btn btn-outline" onClick={refresh}>Làm mới</button></div>
    {!me.roles?.includes('DSP') && <p className="ops-notice">Bạn có quyền xem. Các thao tác điều phối dành cho vai trò DSP.</p>}
    {message && <div className="ops-notice" role="status">{message}<button className="btn btn-outline" onClick={() => setMessage('')}>Đóng thông báo</button></div>}
    {!rental && <nav className="ops-tabs" aria-label="Khu vực vận tải">{[['trips', 'Chuyến vận tải'], ['vehicles', 'Xe'], ['drivers', 'Tài xế'], ['schedule', 'Lịch nguồn lực']].map(([value, name]) => <button key={value} className={`btn ${tab === value ? 'btn-primary' : 'btn-outline'}`} aria-current={tab === value ? 'page' : undefined} onClick={() => setParams(value === 'trips' ? {} : { tab: value })}>{name}</button>)}</nav>}
    {code && tab !== 'schedule' ? <RecordDetail key={`${tab}:${code}`} kind={tab} code={code} me={me} revision={revision} refresh={refresh} back={() => update({ record: null })} notify={setMessage}/>
      : <RecordList key={tab} kind={tab} params={params} update={update} revision={revision} me={me} refresh={refresh} notify={setMessage}/>}
  </div>;
}

function RecordList({ kind, params, update, revision, me, refresh, notify }) {
  const schedule = kind === 'schedule', fleet = ['vehicles', 'drivers'].includes(kind);
  const offset = pageOffset(params.get('offset')), limit = 20;
  const q = params.get('q') || '', status = params.get('status') || '';
  const today = localTime(new Date()).slice(0, 10), day = validDay(params.get('day'), today), endDay = validDay(params.get('end_day'), day);
  const [draft, setDraft] = useState(q), [modal, setModal] = useState(null);
  useEffect(() => setDraft(q), [q]);
  const query = new URLSearchParams({ limit, offset });
  if (schedule) {
    const nextDay = new Date(`${endDay < day ? day : endDay}T00:00:00Z`); nextDay.setUTCDate(nextDay.getUTCDate() + 1);
    query.set('start', apiTime(`${day}T00:00`)); query.set('end', apiTime(`${nextDay.toISOString().slice(0, 10)}T00:00`));
    for (const name of ['vehicle_id', 'driver_id']) if (params.get(name)) query.set(name, params.get(name));
    if (params.get('released') === '1') query.set('include_released', 'true');
  } else {
    if (q) query.set('q', q); if (status) query.set('status', status);
    if (!fleet && params.get('late') === '1') query.set('overdue_only', 'true');
  }
  const { data, loading, error } = useResource(`/${schedule ? 'fe2/schedule' : kind}?${query}`, revision);
  const states = kind === 'trips' ? tripStates : kind === 'rentals' ? rentalStates : kind === 'vehicles' ? ['ACTIVE', 'MAINTENANCE', 'INACTIVE'] : ['ACTIVE', 'INACTIVE'];
  const headers = schedule ? ['Lịch / Hồ sơ / Đơn hàng', 'Dịch vụ', 'Xe', 'Tài xế', 'Bắt đầu', 'Kết thúc', 'Trạng thái'] : kind === 'vehicles' ? ['Biển số', 'Loại xe', 'Tải trọng', 'Trạng thái', 'Sử dụng', ''] : kind === 'drivers' ? ['Mã', 'Họ tên', 'Điện thoại', 'Trạng thái', 'Sử dụng', ''] : ['Mã hồ sơ', 'Đơn hàng', 'Xe', 'Lịch dự kiến', 'Trạng thái', ''];
  return <section className="panel">
    <form className="ops-filters" onSubmit={e => { e.preventDefault(); update({ q: draft, offset: null }); }}>
      {schedule ? <><label>Từ ngày<input type="date" required value={day} onChange={e => { if (e.target.value) update({ day: e.target.value, offset: null }); }}/></label><label>Đến ngày<input type="date" min={day} value={endDay < day ? day : endDay} onChange={e => { if (e.target.value) update({ end_day: e.target.value, offset: null }); }}/></label><label>ID xe<input type="number" min="1" value={params.get('vehicle_id') || ''} onChange={e => update({ vehicle_id: e.target.value, offset: null })}/></label><label>ID tài xế<input type="number" min="1" value={params.get('driver_id') || ''} onChange={e => update({ driver_id: e.target.value, offset: null })}/></label><label className="ops-check"><input type="checkbox" checked={params.get('released') === '1'} onChange={e => update({ released: e.target.checked ? '1' : null, offset: null })}/>Gồm lịch đã giải phóng</label></>
        : <><label>Tìm kiếm<input value={draft} placeholder={fleet ? 'Tên / biển số / số điện thoại' : 'Mã hồ sơ / mã đơn hàng'} onChange={e => setDraft(e.target.value)}/></label><button className="btn btn-outline">Tìm</button><label>Trạng thái<select value={status} onChange={e => update({ status: e.target.value, offset: null })}><option value="">Tất cả</option>{states.map(s => <option key={s} value={s}>{label(s)}</option>)}</select></label>{!fleet && <label className="ops-check"><input type="checkbox" checked={params.get('late') === '1'} onChange={e => update({ late: e.target.checked ? '1' : null, offset: null })}/>Chỉ quá hạn</label>}</>}
      {fleet && me.roles?.includes('DSP') && <button type="button" className="btn btn-primary" onClick={() => setModal(fleetConfig(kind, 'create', {}))}>{kind === 'vehicles' ? 'Thêm xe' : 'Thêm tài xế'}</button>}
    </form>
    <ApiError error={error}/>{loading ? <p role="status">Đang tải dữ liệu…</p> : data && <><div className="table-wrap"><table><thead><tr>{headers.map((h, i) => <th key={i}>{h}</th>)}</tr></thead><tbody>{data.items.map(row => <tr key={row.code || row.id}>
      {schedule ? <><td>{row.code}<br/><button className="ops-link" onClick={() => { window.location.assign(`/app/${row.source_type === 'RENTAL' ? 'thue-xe' : 'van-tai'}?record=${encodeURIComponent(row.source_code)}`); }}>{row.source_code}</button><br/>{row.order_code || '—'}</td><td>{label(row.source_type)}</td><td>{row.vehicle.plate_number} (ID {row.vehicle.id})</td><td>{row.driver?.full_name || '—'}</td><td>{displayTime(row.planned_start)}</td><td>{displayTime(row.planned_end)}</td><td><Status value={row.status}/>{row.overdue && <strong className="ops-late">Quá hạn</strong>}</td></>
        : <>{kind === 'vehicles' ? <><td>{row.plate_number}</td><td>{row.vehicle_type}</td><td>{row.capacity_kg} kg</td></> : kind === 'drivers' ? <><td>{row.code}</td><td>{row.full_name}</td><td>{row.phone}</td></> : <><td>{row.code}</td><td>{row.order_code}</td><td>{row.booking?.vehicle.plate_number || 'Chưa có xe'}</td><td>{displayTime(row.booking?.planned_start || row.requested_start)}<br/>{displayTime(row.booking?.planned_end || row.requested_end)}</td></>}
          <td><Status value={row.status}/>{(row.overdue || row.booking?.overdue) && <strong className="ops-late">Quá hạn</strong>}</td>{fleet && <td>{row.in_use ? 'Đang sử dụng' : 'Không sử dụng'}</td>}<td><button className="btn btn-outline" onClick={() => update({ record: fleet ? row.id : row.code })}>Chi tiết</button></td></>}
    </tr>)}</tbody></table></div>{!data.items.length && <p className="empty">Không có bản ghi phù hợp bộ lọc.</p>}
      <div className="ops-pagination"><span>{data.total} bản ghi · Trang {Math.floor(offset / limit) + 1}</span><button className="btn btn-outline" disabled={offset === 0} onClick={() => update({ offset: Math.max(0, offset - limit) })}>Trước</button><button className="btn btn-outline" disabled={offset + limit >= data.total} onClick={() => update({ offset: offset + limit })}>Sau</button></div></>}
    {modal && <OperationForm config={modal} record={{}} onClose={() => { setModal(null); refresh(); }} onSaved={() => { setModal(null); notify('Đã tạo hồ sơ.'); refresh(); }} onConflict={refresh}/>}
  </section>;
}

function fleetConfig(kind, action, row) {
  const vehicle = kind === 'vehicles';
  const fields = vehicle ? [field('plate_number', 'Biển số', 'text', { maxLength: 20 }), field('vehicle_type', 'Mã loại xe', 'text', { maxLength: 50 }), field('capacity_kg', 'Tải trọng (kg)', 'number', { min: 1 }), field('description', 'Mô tả', 'textarea', { required: false, nullable: false })] : [field('full_name', 'Họ tên'), field('phone', 'Điện thoại', 'tel', { maxLength: 30 })];
  const statuses = (vehicle ? ['ACTIVE', 'MAINTENANCE', 'INACTIVE'] : ['ACTIVE', 'INACTIVE']).filter(s => action !== 'status' || s !== row.status);
  return { title: action === 'create' ? (vehicle ? 'Thêm xe' : 'Thêm tài xế') : action === 'status' ? 'Đổi trạng thái' : 'Sửa thông tin',
    method: action === 'edit' ? 'PATCH' : 'POST', path: `/${kind}${action === 'create' ? '' : `/${row.id}${action === 'status' ? '/status' : ''}`}`,
    version: action === 'create' ? undefined : row.version, initial: { ...row, ...(action === 'status' ? { status: '' } : { status: row.status || 'ACTIVE' }) },
    fields: action === 'status' ? [field('status', 'Trạng thái mới', 'select', { options: statuses }), field('reason', 'Lý do', 'textarea')] : [...fields, ...(action === 'create' ? [field('status', 'Trạng thái', 'select', { options: statuses })] : [])] };
}

function RecordDetail({ kind, code, me, revision, refresh, back, notify }) {
  const { data: row, loading, error } = useResource(`/${kind}/${encodeURIComponent(code)}`, revision);
  const [modal, setModal] = useState(null);
  const fleet = ['vehicles', 'drivers'].includes(kind), rental = kind === 'rentals', writer = me.roles?.includes('DSP');
  const commercial = useResource(fleet ? null : `/fe2/${kind}/${encodeURIComponent(code)}/commercial-summary`, revision);
  const show = (action, record = row) => setModal({ config: fleet ? fleetConfig(kind, action, record) : actionConfig(kind, action, record), record });
  return <><button className="btn btn-outline ops-back" onClick={back}>Về danh sách</button><ApiError error={error}/>{loading && <p role="status">Đang tải chi tiết…</p>}{row && <>
    <section className="panel"><div className="panel-heading"><h2>{row.code || row.plate_number} <Status value={row.status}/></h2>{(row.overdue || row.booking?.overdue) && <strong className="ops-late">Quá hạn</strong>}</div>
      {fleet ? <Facts items={kind === 'vehicles' ? [['ID xe', row.id], ['Biển số', row.plate_number], ['Loại xe', row.vehicle_type], ['Tải trọng', `${row.capacity_kg} kg`], ['Mô tả', row.description], ['Sử dụng', row.in_use ? 'Đang sử dụng' : 'Không sử dụng']] : [['ID tài xế', row.id], ['Họ tên', row.full_name], ['Điện thoại', row.phone], ['Sử dụng', row.in_use ? 'Đang sử dụng' : 'Không sử dụng']]}/>
        : <><Facts items={rental ? [['Đơn hàng', row.order_code], ['Loại xe', row.vehicle_type], ['Tải trọng tối thiểu', `${row.min_capacity_kg} kg`], ['Yêu cầu từ', displayTime(row.requested_start)], ['Yêu cầu đến', displayTime(row.requested_end)], ['Trễ', `${row.late_minutes || 0} phút (không tự tính phí)`]] : [['Đơn hàng', row.order_code], ['Khách hàng', row.customer_code], ['Hàng hóa', row.cargo_description], ['Khối lượng', `${row.cargo_weight_kg} kg`], ['Lấy hàng', row.pickup_address], ['Giao hàng', row.delivery_address], ['Lấy tại kho', row.pickup_from_warehouse ? 'Có' : 'Không'], ['Cam kết giao', displayTime(row.committed_end)]]}/>
          {row.booking && <Facts items={ [['Xe', row.booking.vehicle.plate_number], ['Tài xế', row.booking.driver?.full_name], ['Bắt đầu dự kiến', displayTime(row.booking.planned_start)], ['Kết thúc dự kiến', displayTime(row.booking.planned_end)], ['Bắt đầu thực tế', displayTime(row.booking.actual_start)], ['Kết thúc thực tế', displayTime(row.booking.actual_end)]] }/>}</>}
      <div className="ops-actions">{fleet ? writer && <><button className="btn btn-primary" onClick={() => show('edit')}>Sửa thông tin</button>{!row.in_use && <button className="btn btn-outline" onClick={() => show('status')}>Đổi trạng thái</button>}</> : actionsFor(kind, row, me.roles).filter(action => action !== 'complete' || (!commercial.loading && !(commercial.data?.pending_charges > 0))).map(action => <button className="btn btn-outline" key={action} onClick={() => show(action)}>{actionConfig(kind, action, row).title}</button>)}</div>
    </section>
    {!fleet && <CommercialSummary state={commercial} refresh={refresh}/>}
    {!fleet && (rental ? <><Inspection title="Biên bản bàn giao" value={row.handover}/><Inspection title="Biên bản trả xe" value={row.return}/><section className="panel"><h2>Ghi chú bổ sung</h2>{row.notes.map((n, i) => <article className="ops-event" key={i}><p>{n.note}</p><small>{n.author} · {displayTime(n.created_at)}</small></article>)}{!row.notes.length && <p>Chưa có ghi chú.</p>}</section></>
      : <><section className="panel"><h2>Mốc vận chuyển</h2>{row.milestones.map(m => <article className="ops-event" key={m.milestone}><h3>{label(m.milestone)}</h3><Facts items={ [['Thực tế', displayTime(m.actual_at)], ['Ghi nhận', `${displayTime(m.recorded_at)} · ${m.recorded_by}`], ['Người giao / nhận', m.person_name], ['Ghi chú', m.note], ...(m.details?.cargo_condition ? [['Tình trạng hàng', m.details.cargo_condition], ['Khối lượng thực tế', m.details.actual_weight_kg == null ? '—' : `${m.details.actual_weight_kg} kg`]] : [])] }/></article>)}{!row.milestones.length && <p>Chưa ghi nhận mốc vận chuyển.</p>}</section>
        <section className="panel"><h2>Bằng chứng giao hàng</h2><Evidence files={row.delivery_evidence}/>{!row.delivery_evidence.length && <p>Chưa có bằng chứng giao hàng.</p>}</section>
        <section className="panel"><h2>Sự cố ({row.open_incidents} chưa xử lý)</h2>{row.incidents.map(incident => <article className="ops-event" key={incident.code}><h3>{incident.code} · {label(incident.incident_type)} <Status value={incident.status}/></h3><Facts items={ [['Mô tả', incident.description], ['Người xử lý', incident.handler_name], ['Xảy ra', displayTime(incident.occurred_at)], ['Dự kiến xử lý', displayTime(incident.expected_resolution_at)], ['Cách xử lý', incident.resolution], ['Xử lý xong', displayTime(incident.resolved_at)]] }/>{writer && incident.status === 'OPEN' && <button className="btn btn-outline" onClick={() => show('resolve', incident)}>Xử lý sự cố</button>}</article>)}{!row.incidents.length && <p>Chưa có sự cố.</p>}</section></>)}
  </>}{modal && <OperationForm config={modal.config} record={modal.record} onClose={() => { setModal(null); refresh(); }} onSaved={() => { setModal(null); notify('Đã ghi nhận thao tác thành công.'); refresh(); }} onConflict={refresh}/>}</>;
}

function Inspection({ title, value }) {
  return <section className="panel"><h2>{title}</h2>{value ? <><p>Biên bản đã xác nhận; chỉ có thể bổ sung ghi chú.</p><Facts items={ [['Thực tế', displayTime(value.actual_at)], ['Người giao / nhận', value.person_name], ['Số kilomet', value.odometer_km], ['Tình trạng xe', value.body_condition], ['Nhiên liệu', value.fuel_level], ['Phụ kiện', value.accessories], ['Ghi chú', value.note], ['Người ghi nhận', value.recorded_by], ['Ghi nhận lúc', displayTime(value.recorded_at)]] }/><Evidence files={value.attachments}/></> : <p>Chưa lập biên bản.</p>}</section>;
}

function CommercialSummary({ state: { data, loading, error }, refresh }) {
  return <section className="panel"><h2>Đơn hàng, phụ phí và thanh toán</h2>
    <p>Thông tin chỉ đọc. Hoàn tất vận hành không yêu cầu đã thu đủ tiền.</p>
    {loading && <p role="status">Đang tải thông tin đơn hàng…</p>}
    <ApiError error={error}/>{error && <button className="btn btn-outline" onClick={refresh}>Tải lại thông tin đơn</button>}
    {data && !data.available && <p>Hồ sơ mẫu chưa có đơn hàng liên kết trong phân hệ kinh doanh. Chưa có số dư để đối chiếu.</p>}
    {data?.available && <><Facts items={ [['Trạng thái đơn', label(data.balance.order_status)], ['Thanh toán', label(data.balance.payment_status)], ['Giá đã chốt', money(data.balance.quoted_vnd)], ['Phụ phí đã duyệt', money(data.balance.approved_charges_vnd)], ['Đã thu', money(data.balance.amount_paid_vnd)], ['Còn phải thu', money(data.balance.balance_vnd)]] }/>
      {data.pending_charges > 0 && <p className="ops-notice">Còn {data.pending_charges} phụ phí chờ duyệt. Cần xử lý trước khi hoàn tất hồ sơ.</p>}
      {data.charges.map(charge => <article className="ops-event" key={charge.code}><h3>{charge.code} · {money(charge.amount_vnd)} <Status value={charge.status}/></h3><Facts items={ [['Lý do', charge.reason], ['Đề nghị bởi', charge.created_by], ['Ngày đề nghị', displayTime(charge.created_at)], ['Quyết định', charge.decision_note], ['Người duyệt', charge.decided_by], ['Ngày duyệt', displayTime(charge.decided_at)]] }/></article>)}
      {!data.charges.length && <p>Chưa có phụ phí.</p>}</>}
  </section>;
}
