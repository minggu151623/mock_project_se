import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, money } from '../api';
import { ApiError } from './OperationForm';
import { displayTime, label, pageOffset, validDay } from './operations';
import './operations.css';
import './dashboard.css';

const amountMetrics = new Set(['receivables_balance', 'payments_period']);
const reports = [
  ['orders', 'Đơn hàng', ['FIN', 'SAL', 'MGR']],
  ['schedule', 'Lịch xe', ['DSP', 'MGR']],
  ['stock', 'Tồn kho', ['WHS', 'MGR']],
  ['receivables', 'Công nợ', ['FIN', 'SAL', 'MGR']],
];
const names = { code: 'Mã', order_code: 'Đơn', customer_code: 'Khách', service_type: 'Dịch vụ', status: 'Trạng thái', created_at: 'Tạo lúc', committed_end: 'Hạn chuyến', requested_end: 'Hạn trả', planned_end: 'Kết thúc lịch giữ', payment_due_date: 'Hạn thu', amount_vnd: 'Còn phải thu (VND)', signed_amount_vnd: 'Thu ròng (VND)', occurred_at: 'Thời điểm thu/đảo', plate_number: 'Biển số', reversal_of: 'Đảo khoản', method: 'Phương thức' };

function useLoad(path, revision) {
  const [state, setState] = useState({ loading: true });
  useEffect(() => { if (!path) { setState({ loading: false }); return; }
    let active = true; setState({ loading: true });
    api(path).then(data => { if (active) setState({ data, loading: false }); }).catch(error => { if (active) setState({ error, loading: false }); });
    return () => { active = false; };
  }, [path, revision]);
  return state;
}

function cell(key, value) {
  if (value == null) return '—';
  if (key.endsWith('_at') || key.endsWith('_end')) return displayTime(value);
  if (key === 'payment_due_date') return value.slice(5, 7) + '/' + value.slice(8, 10) + '/' + value.slice(0, 4);
  if (key === 'amount_vnd' || key === 'signed_amount_vnd') return money(value);
  return label(value);
}

export default function DashboardPage({ me }) {
  const [params, setParams] = useSearchParams(), [revision, setRevision] = useState(0), [exporting, setExporting] = useState(''), [exportError, setExportError] = useState(null);
  const start = validDay(params.get('start'), ''), end = validDay(params.get('end'), '');
  const metric = params.get('metric') || '', offset = pageOffset(params.get('offset')), limit = 20;
  const query = new URLSearchParams(); if (start) query.set('start', start); if (end) query.set('end', end);
  const summary = useLoad(`/fe2/dashboard?${query}`, revision);
  const detail = useLoad(metric ? `/fe2/dashboard/${encodeURIComponent(metric)}?${query}&limit=${limit}&offset=${offset}` : null, revision);
  const update = patch => { const next = new URLSearchParams(params); Object.entries(patch).forEach(([key, value]) => value == null || value === '' ? next.delete(key) : next.set(key, String(value))); setParams(next); };
  const exportReport = async kind => {
    setExporting(kind); setExportError(null);
    try {
      const blob = await api(`/fe2/reports/${kind}.csv?${query}`);
      const url = URL.createObjectURL(blob), anchor = document.createElement('a');
      const exportedAt = new Date().toISOString().replace(/[:.]/g, '-');
      anchor.href = url; anchor.download = `${kind}_${start || 'all'}_${end || 'all'}_${exportedAt}.csv`; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
    } catch (error) { setExportError(error); } finally { setExporting(''); }
  };
  const rows = detail.data?.items || [], columns = Object.keys(rows[0] || {});
  return <div className="ops-page dashboard-page"><div className="page-heading"><div><span className="eyebrow">BÁO CÁO</span><h1>Dashboard</h1><p>Snapshot hiện tại và chỉ số phát sinh theo khoảng ngày, trong phạm vi quyền của bạn.</p></div><button className="btn btn-outline" onClick={() => setRevision(x => x + 1)}>Làm mới</button></div>
    <section className="panel"><div className="ops-filters"><label>Từ ngày<input type="date" value={start} max={end || undefined} onChange={e => update({ start: e.target.value, metric: null, offset: null })}/></label><label>Đến ngày<input type="date" value={end} min={start || undefined} onChange={e => update({ end: e.target.value, metric: null, offset: null })}/></label><button className="btn btn-outline" onClick={() => update({ start: null, end: null, metric: null, offset: null })}>Xóa lọc ngày</button></div><p>Khoảng ngày tính theo giờ Việt Nam, gồm hết ngày kết thúc. Chỉ áp dụng cho thẻ ghi “Trong kỳ”; các thẻ khác là số liệu tại thời điểm xem.</p><ApiError error={summary.error}/>{summary.loading ? <p role="status">Đang tải chỉ số…</p> : summary.data && <><p>Cập nhật: {displayTime(summary.data.updated_at)}</p><div className="dashboard-grid">{summary.data.metrics.map(item => <button key={item.metric} className={`dashboard-card ${metric === item.metric ? 'selected' : ''}`} onClick={() => update({ metric: item.metric, offset: null })}><span>{item.title}</span><strong>{amountMetrics.has(item.metric) ? money(item.value) : item.value}</strong><small>{item.period ? 'Trong kỳ' : 'Snapshot hiện tại'} · Xem danh sách</small></button>)}</div></>}</section>
    {metric && <section className="panel"><div className="dashboard-heading"><h2>{detail.data?.title || 'Danh sách chi tiết'}</h2><button className="btn btn-outline" onClick={() => update({ metric: null, offset: null })}>Đóng</button></div><ApiError error={detail.error}/>{detail.loading ? <p role="status">Đang tải danh sách…</p> : detail.data && <><p>{detail.data.total} bản ghi · Chỉ số {amountMetrics.has(metric) ? money(detail.data.value) : detail.data.value}</p><div className="table-wrap"><table><thead><tr>{columns.map(key => <th key={key}>{names[key] || key}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={row.code || row.id || index}>{columns.map(key => <td key={key}>{cell(key, row[key])}</td>)}</tr>)}</tbody></table></div>{!rows.length && <p>Không có bản ghi trong điều kiện này.</p>}<div className="ops-pagination"><span>Trang {Math.floor(offset/limit)+1}</span><button disabled={!offset} onClick={() => update({ offset: Math.max(0,offset-limit) })}>Trước</button><button disabled={offset+limit >= detail.data.total} onClick={() => update({ offset: offset+limit })}>Sau</button></div></>}</section>}
    <section className="panel"><h2>Xuất CSV theo bộ lọc</h2><p>Xuất toàn bộ kết quả phù hợp, không chỉ trang đang xem. File có thời điểm xuất và khoảng ngày trong tên do máy chủ đặt.</p><div className="ops-actions">{reports.filter(([, , roles]) => roles.some(role => me.roles?.includes(role))).map(([kind, title]) => <button className="btn btn-outline" key={kind} disabled={!!exporting} onClick={() => exportReport(kind)}>{exporting === kind ? 'Đang xuất…' : `Xuất ${title}`}</button>)}</div><ApiError error={exportError}/></section>
  </div>;
}
