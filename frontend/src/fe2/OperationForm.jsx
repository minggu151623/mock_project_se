import { useEffect, useRef, useState } from 'react';
import { api, upload } from '../api';
import { apiTime, buildBody, displayTime, label, localTime, requestKey } from './operations';

export function ApiError({ error }) {
  if (!error) return null;
  const conflicts = error.details?.conflicts || error.details?.bookings;
  return <div className="inline-error ops-error" role="alert"><b>{error.message || String(error)}</b>{error.code && <small>{error.code}</small>}
    {conflicts?.length > 0 ? <div className="table-wrap"><table><thead><tr><th>Hồ sơ</th><th>Nguồn lực</th><th>Bắt đầu</th><th>Kết thúc</th><th>Trạng thái</th></tr></thead><tbody>{conflicts.map((item, index) => <tr key={index}><td><a href={`/app/${item.source_type === 'RENTAL' ? 'thue-xe' : 'van-tai'}?record=${encodeURIComponent(item.source_code)}`} target="_blank" rel="noreferrer">{item.source_code}</a><br/>{item.booking_code}</td><td>{item.resource === 'DRIVER' ? 'Tài xế' : 'Xe'}</td><td>{displayTime(item.planned_start)}</td><td>{displayTime(item.planned_end)}</td><td>{label(item.status)}</td></tr>)}</tbody></table></div>
      : error.details && Object.keys(error.details).length > 0 && <details><summary>Thông tin lỗi</summary><pre>{JSON.stringify(error.details, null, 2)}</pre></details>}
  </div>;
}

export default function OperationForm({ config, record, onClose, onSaved, onConflict }) {
  const startsWork = /\/(pickup|handover)$/.test(config.path);
  const heldStart = record.booking?.planned_start, heldEnd = record.booking?.planned_end;
  const withinHoldNow = heldStart && heldEnd && Date.now() >= new Date(heldStart).getTime() && Date.now() < new Date(heldEnd).getTime();
  const [values, setValues] = useState(() => ({ ...Object.fromEntries(config.fields.filter(f => ['actual_at', 'released_at', 'occurred_at', 'resolved_at'].includes(f.name)).map(f => [f.name, startsWork && !withinHoldNow ? '' : localTime(new Date())])), ...config.initial }));
  const [files, setFiles] = useState([]), [error, setError] = useState(null), [busy, setBusy] = useState(false);
  const [options, setOptions] = useState({}), [searching, setSearching] = useState(false), [searched, setSearched] = useState(false);
  const [uncertain, setUncertain] = useState(false), [stale, setStale] = useState(false);
  const attempt = useRef(null), lock = useRef(false), dialog = useRef(null);
  useEffect(() => { const previous = document.activeElement; dialog.current?.focus(); return () => previous?.focus(); }, []);
  const change = (name, value) => { setValues(old => ({ ...old, [name]: value })); if (['planned_start', 'planned_end'].includes(name)) { setSearched(false); setOptions({}); setValues(old => ({ ...old, vehicle_id: '', driver_id: '' })); } };
  const search = async () => {
    setError(null); setSearching(true); setSearched(false);
    try {
      if (!values.planned_start || !values.planned_end || values.planned_end <= values.planned_start) throw new Error('Chọn khoảng thời gian có kết thúc sau bắt đầu.');
      const query = new URLSearchParams({ start: apiTime(values.planned_start), end: apiTime(values.planned_end), min_capacity_kg: record.cargo_weight_kg || record.min_capacity_kg || 0 });
      if (config.rental) query.set('vehicle_type', record.vehicle_type);
      const vehicleResult = await api(`/availability/vehicles?${query}`);
      const driverResult = config.rental ? { items: [] } : await api(`/availability/drivers?${new URLSearchParams({ start: apiTime(values.planned_start), end: apiTime(values.planned_end) })}`);
      // The availability API includes this record's old hold. Offer its current resources
      // when changing a hold; the backend checks the replacement atomically on submit.
      const vehicles = [...vehicleResult.items]; const drivers = [...driverResult.items];
      if (config.version !== undefined && record.booking?.vehicle && !vehicles.some(v => v.id === record.booking.vehicle.id)) vehicles.unshift(record.booking.vehicle);
      if (config.version !== undefined && record.booking?.driver && !drivers.some(d => d.id === record.booking.driver.id)) drivers.unshift(record.booking.driver);
      setOptions({ vehicle_id: vehicles.map(v => [v.id, `${v.plate_number} · ${v.vehicle_type}${v.capacity_kg ? ` · ${v.capacity_kg} kg` : ' · xe hiện tại (kiểm tra lại khi lưu)'}`]), driver_id: drivers.map(d => [d.id, `${d.full_name}${d.phone ? ` · ${d.phone}` : ' · tài xế hiện tại (kiểm tra lại khi lưu)'}`]) }); setSearched(true);
    } catch (e) { setError(e); } finally { setSearching(false); }
  };
  const addFiles = async e => {
    const selected = [...e.target.files]; e.target.value = '';
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(null);
    try {
      if (selected.length + files.length > 20) throw new Error('Tối đa 20 tệp.');
      for (const file of selected) {
        if (file.size > 5 * 1024 * 1024) throw new Error(`${file.name}: tệp phải nhỏ hơn hoặc bằng 5 MB.`);
        if (file.type && !['image/png', 'image/jpeg', 'application/pdf'].includes(file.type)) throw new Error('Chỉ nhận tệp PNG, JPEG hoặc PDF.');
        const stored = await upload(file); setFiles(old => [...old, stored]);
      }
    } catch (e) { setError(e); } finally { lock.current = false; setBusy(false); }
  };
  const submit = async e => {
    e.preventDefault(); if (lock.current || stale || (uncertain && !config.idempotent)) return;
    setError(null);
    if (config.booking && !searched) { setError(new Error('Hãy tìm nguồn lực trống cho khoảng thời gian đã chọn.')); return; }
    if (config.evidence && (config.evidence !== 'delivery' || values.result === 'SUCCESS') && !files.length) { setError(new Error('Cần ít nhất một tệp bằng chứng.')); return; }
    if (config.evidence === 'delivery' && values.result === 'SUCCESS' && !values.receiver_name?.trim()) { setError(new Error('Cần tên người nhận hàng.')); return; }
    if (config.evidence === 'delivery' && values.result === 'FAILED' && !values.note?.trim()) { setError(new Error('Cần lý do giao không thành công.')); return; }
    lock.current = true; setBusy(true);
    const body = buildBody(config, values, files);
    attempt.current = requestKey(attempt.current, config.path, config.method, body);
    try {
      await api(config.path, { method: config.method, signal: AbortSignal.timeout(20000), ...(config.fields.length || config.evidence ? { body } : {}), headers: { 'Idempotency-Key': attempt.current.key } });
    } catch (e) {
      setError(e);
      setUncertain(!e.status || e.status >= 500);
      if (['VERSION_CONFLICT', 'INVALID_STATE'].includes(e.code)) { setStale(true); onConflict(); }
      lock.current = false; setBusy(false); return;
    }
    lock.current = false; setBusy(false); onSaved();
  };
  const fieldError = name => error?.details?.field === name || error?.details?.fields?.some(f => f.field === name);
  const freeze = busy || searching || uncertain || stale;
  return <div className="modal-backdrop"><div className="modal-card ops-modal" role="dialog" aria-modal="true" aria-labelledby="operation-title" tabIndex={-1} ref={dialog}
    onKeyDown={e => { if (e.key === 'Escape' && !busy && !searching && !uncertain) onClose(); if (e.key === 'Tab') { const controls = [...dialog.current.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary')].filter(el => el.getClientRects().length); const first = controls[0], last = controls.at(-1); if (!controls.length) { e.preventDefault(); return; } if (e.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) { e.preventDefault(); last?.focus(); } else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); } } }}>
    <h2 id="operation-title">{config.title}</h2><p>{record.code || record.plate_number || record.full_name || 'Hồ sơ mới'}</p>
    {startsWork && heldStart && heldEnd && <p role="note">Khung giờ đã giữ: <strong>{displayTime(heldStart)} – {displayTime(heldEnd)}</strong>. Thời điểm thực tế phải nằm trong khoảng này và không được ở tương lai. Nếu khung giờ không phù hợp, đóng form và chọn Đổi phân công hoặc Đổi giữ xe.</p>}
    <form onSubmit={submit}><fieldset disabled={freeze}>
      {config.fields.map(f => <label key={f.name}>{f.title}{f.required ? ' *' : ''}
        {f.type === 'select' ? <select name={f.name} value={values[f.name] ?? ''} required={f.required} aria-invalid={!!fieldError(f.name)} onChange={e => change(f.name, e.target.value)}><option value="">Chọn…</option>{(options[f.name] || f.options || []).map(option => { const [v, text] = Array.isArray(option) ? option : [option, label(option)]; return <option value={v} key={v}>{text}</option>; })}</select>
          : f.type === 'textarea' ? <textarea name={f.name} required={f.required} maxLength={2000} value={values[f.name] ?? ''} aria-invalid={!!fieldError(f.name)} onChange={e => change(f.name, e.target.value)} />
            : <input name={f.name} type={f.type} min={f.min} step={f.step || (f.type === 'number' ? 1 : undefined)} maxLength={f.maxLength || 200} required={f.required} value={values[f.name] ?? ''} aria-invalid={!!fieldError(f.name)} onChange={e => change(f.name, e.target.value)} />}
        {fieldError(f.name) && <small className="ops-field-error">{error.message}</small>}
      </label>)}
      {config.booking && <><button className="btn btn-outline" type="button" onClick={() => { setValues(old => ({ ...old, planned_start: localTime(new Date()), planned_end: localTime(new Date(Date.now() + 3600000)), vehicle_id: '', driver_id: '' })); setSearched(false); setOptions({}); }}>Chọn từ bây giờ trong 1 giờ</button><button className="btn btn-outline" type="button" disabled={searching} onClick={search}>{searching ? 'Đang tìm…' : 'Tìm xe / tài xế trống'}</button>{searched && <p role="status">{options.vehicle_id?.length || 0} xe{!config.rental && `, ${options.driver_id?.length || 0} tài xế`}. Lịch được kiểm tra lại khi xác nhận.</p>}</>}
      {config.evidence && <label>{config.evidence === 'inspection' ? 'Ảnh / biên bản (PNG/JPEG/PDF)' : 'Bằng chứng giao hàng (PNG/JPEG/PDF)'}<input type="file" multiple accept="image/png,image/jpeg,application/pdf" onChange={addFiles}/><small>Mỗi tệp tối đa 5 MB. {files.length}/20 tệp đã tải.</small></label>}
      {files.map(file => <div className="ops-file" key={file.code}>{file.original_name}<button type="button" className="btn btn-outline" onClick={() => setFiles(old => old.filter(item => item.code !== file.code))}>Bỏ tệp</button></div>)}
    </fieldset>
    {!config.fields.length && <p>Xác nhận hoàn tất hồ sơ? Backend sẽ kiểm tra đủ biên bản và phụ phí trước khi hoàn tất.</p>}
    <ApiError error={error}/>
    {uncertain && <p role="alert">Chưa xác định máy chủ đã ghi nhận hay chưa. {config.idempotent ? 'Bấm thử lại để gửi đúng yêu cầu và khóa xác nhận cũ.' : 'Đóng và tải lại hồ sơ, kiểm tra kết quả trước khi thực hiện lại.'}</p>}
    {stale && <p role="alert">Dữ liệu đã thay đổi. Đã yêu cầu tải lại hồ sơ; hãy đóng biểu mẫu và kiểm tra trước khi thao tác tiếp.</p>}
    <div className="modal-actions"><button className="btn btn-outline" type="button" disabled={busy || searching || (uncertain && config.idempotent)} onClick={onClose}>Đóng</button><button className="btn btn-primary" disabled={busy || searching || stale || (uncertain && !config.idempotent)}>{busy ? 'Đang xử lý…' : uncertain ? 'Thử lại cùng yêu cầu' : 'Xác nhận'}</button></div></form>
  </div></div>;
}
