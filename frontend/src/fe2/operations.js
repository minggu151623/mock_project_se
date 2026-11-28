export const labels = {
  UNASSIGNED: 'Chưa phân công', ASSIGNED: 'Đã phân công', PICKED_UP: 'Đã nhận hàng', IN_TRANSIT: 'Đang vận chuyển',
  DELIVERED: 'Đã giao hàng', COMPLETED: 'Hoàn tất', CANCELLED: 'Đã hủy', RESERVED: 'Đã giữ xe',
  HANDED_OVER: 'Đã bàn giao', RETURNED: 'Đã trả xe', ACTIVE: 'Hoạt động', INACTIVE: 'Ngừng hoạt động',
  MAINTENANCE: 'Bảo trì', HELD: 'Đang giữ', IN_USE: 'Đang sử dụng', RELEASED: 'Đã giải phóng',
  OPEN: 'Chưa xử lý', RESOLVED: 'Đã xử lý', DELAY: 'Chậm tiến độ', BREAKDOWN: 'Hỏng xe',
  DAMAGE: 'Hư hỏng hàng', DELIVERY_FAILED: 'Giao không thành công', OTHER: 'Khác', SUCCESS: 'Thành công', FAILED: 'Không thành công',
  TRANSPORT: 'Vận tải', RENTAL: 'Thuê xe', CONFIRMED: 'Đã xác nhận', IN_PROGRESS: 'Đang thực hiện',
  PENDING: 'Chờ duyệt', APPROVED: 'Đã duyệt', REJECTED: 'Từ chối', UNPAID: 'Chưa thu', PARTIALLY_PAID: 'Thu một phần', PAID: 'Đã thu đủ', NOT_APPLICABLE: 'Không áp dụng',
};
export const label = value => labels[value] || value || '—';
export const tripStates = ['UNASSIGNED', 'ASSIGNED', 'PICKED_UP', 'IN_TRANSIT', 'DELIVERED', 'COMPLETED', 'CANCELLED'];
export const rentalStates = ['UNASSIGNED', 'RESERVED', 'HANDED_OVER', 'RETURNED', 'COMPLETED', 'CANCELLED'];
export const localTime = value => value ? new Date(new Date(value).getTime() + 7 * 3600000).toISOString().slice(0, 19) : '';
export const apiTime = value => value ? `${value}${value.length === 16 ? ':00' : ''}+07:00` : null;
export function validDay(value, fallback) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) return fallback;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value ? value : fallback;
}
export function pageOffset(value) {
  const offset = Number(value);
  return Number.isSafeInteger(offset) && offset >= 0 ? offset : 0;
}
export function displayTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return '—';
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Ho_Chi_Minh', month: '2-digit', day: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(date).map(part => [part.type, part.value]));
  return `${parts.hour}:${parts.minute} ${parts.month}/${parts.day}/${parts.year}`;
}
export const field = (name, title, type = 'text', extra = {}) => ({ name, title, type, required: true, ...extra });
const reason = () => field('reason', 'Lý do', 'textarea');
const time = (name = 'actual_at', title = 'Thời điểm thực tế') => field(name, `${title} (giờ Việt Nam)`, 'datetime-local', { step: 1 });
const note = () => field('note', 'Ghi chú', 'textarea', { required: false });

export function actionsFor(kind, record, roles = []) {
  if (!roles.includes('DSP')) return [];
  if (kind === 'trips') {
    const result = ({ UNASSIGNED: ['assign'], ASSIGNED: ['reassign', 'release', 'pickup', 'extend'], PICKED_UP: ['transit', 'extend'], IN_TRANSIT: ['delivery', 'extend'], DELIVERED: ['complete'] })[record.status] || [];
    return ['COMPLETED', 'CANCELLED'].includes(record.status) ? result : [...result, 'incident'];
  }
  const result = ({ UNASSIGNED: ['reserve'], RESERVED: ['change', 'release', 'handover'], HANDED_OVER: ['return'], RETURNED: ['complete'] })[record.status] || [];
  return record.handover ? [...result, 'note'] : result;
}

export function actionConfig(kind, action, record) {
  const root = `/${kind}/${encodeURIComponent(record.code)}`;
  const rental = kind === 'rentals';
  const base = { method: 'POST', initial: {}, fields: [], path: root, title: '', idempotent: false };
  if (['assign', 'reassign', 'reserve', 'change'].includes(action)) {
    const editing = ['reassign', 'change'].includes(action);
    return { ...base, title: rental ? (editing ? 'Đổi xe / lịch thuê' : 'Giữ xe') : (editing ? 'Đổi phân công' : 'Phân công'),
      path: `${root}/${rental ? 'reservation' : 'assignment'}`, method: editing ? 'PUT' : 'POST', idempotent: !editing,
      booking: true, rental, version: editing ? record.version : undefined,
      initial: { planned_start: localTime(record.booking?.planned_start || record.requested_start || new Date()), planned_end: localTime(record.booking?.planned_end || record.requested_end || new Date(Date.now() + 3600000)), vehicle_id: record.booking?.vehicle.id || '', driver_id: record.booking?.driver?.id || '' },
      fields: [time('planned_start', 'Bắt đầu dự kiến'), time('planned_end', 'Kết thúc dự kiến'), field('vehicle_id', 'Xe', 'select', { numeric: true, options: [] }), ...(!rental ? [field('driver_id', 'Tài xế', 'select', { numeric: true, options: [] })] : []), ...(editing ? [reason(), ...(rental ? [field('customer_note', 'Ghi chú trao đổi với khách', 'textarea')] : [])] : [])] };
  }
  if (action === 'release') return { ...base, title: rental ? 'Hủy giữ xe' : 'Gỡ phân công', path: `${root}/${rental ? 'reservation' : 'assignment'}/release`, version: record.version,
    fields: [reason(), ...(rental ? [field('customer_note', 'Ghi chú trao đổi với khách', 'textarea')] : [])] };
  if (action === 'extend') return { ...base, title: 'Đổi giờ kết thúc dự kiến', path: `${root}/planned-end`, version: record.version, initial: { new_planned_end: localTime(record.booking?.planned_end) }, fields: [time('new_planned_end', 'Kết thúc dự kiến mới'), reason()] };
  if (action === 'pickup') return { ...base, title: 'Nhận hàng', path: `${root}/pickup`, idempotent: true, fields: [time(), field('handed_over_by', 'Người giao hàng'), field('cargo_condition', 'Số lượng / tình trạng hàng', 'textarea'), field('actual_weight_kg', 'Khối lượng thực tế (kg)', 'number', { min: 0.001, step: 'any', required: false }), field('discrepancy_note', 'Ghi chú chênh lệch', 'textarea', { required: false })] };
  if (action === 'transit') return { ...base, title: 'Bắt đầu vận chuyển', path: `${root}/in-transit`, idempotent: true, fields: [time()] };
  if (action === 'delivery') return { ...base, title: 'Ghi nhận giao hàng', path: `${root}/delivery`, idempotent: true, evidence: 'delivery', initial: { result: 'SUCCESS' }, fields: [time(), field('result', 'Kết quả', 'select', { options: ['SUCCESS', 'FAILED'] }), field('receiver_name', 'Người nhận', 'text', { required: false }), note()] };
  if (action === 'complete') return { ...base, title: 'Hoàn tất hồ sơ', path: `${root}/complete`, idempotent: true, fields: rental ? [] : [time('released_at', 'Thời điểm giải phóng xe')] };
  if (action === 'incident') return { ...base, title: 'Ghi nhận sự cố', path: `${root}/incidents`, initial: { incident_type: 'DELAY' }, fields: [field('incident_type', 'Loại sự cố', 'select', { options: ['DELAY', 'BREAKDOWN', 'DAMAGE', 'DELIVERY_FAILED', 'OTHER'] }), time('occurred_at', 'Thời điểm xảy ra'), field('description', 'Mô tả', 'textarea'), field('handler_name', 'Người xử lý'), { ...time('expected_resolution_at', 'Dự kiến xử lý xong'), required: false }] };
  if (action === 'resolve') return { ...base, title: 'Xử lý sự cố', path: `/incidents/${record.code}/resolve`, version: record.version, fields: [field('resolution', 'Cách xử lý', 'textarea'), time('resolved_at', 'Thời điểm xử lý xong')] };
  if (['handover', 'return'].includes(action)) return { ...base, title: action === 'handover' ? 'Bàn giao xe' : 'Nhận lại xe', path: `${root}/${action}`, idempotent: true, evidence: 'inspection', fields: [time(), field(action === 'handover' ? 'receiver_name' : 'returned_by', action === 'handover' ? 'Người nhận xe' : 'Người trả xe'), field('odometer_km', 'Số kilomet', 'number', { min: action === 'return' ? record.handover?.odometer_km || 0 : 0 }), field('body_condition', 'Tình trạng xe', 'textarea'), field('fuel_level', 'Mức nhiên liệu'), field('accessories', 'Phụ kiện', 'textarea'), note()] };
  return { ...base, title: 'Thêm ghi chú biên bản', path: `${root}/notes`, fields: [field('note', 'Ghi chú bổ sung', 'textarea')] };
}

export function buildBody(config, values, files = []) {
  const body = {};
  for (const f of config.fields) {
    const v = values[f.name];
    if (v === '' || v == null) { if (!f.required) body[f.name] = f.nullable === false ? '' : null; continue; }
    body[f.name] = f.type === 'datetime-local' ? apiTime(v) : (f.type === 'number' || f.numeric) ? Number(v) : String(v).trim();
  }
  if (config.version !== undefined) body.version = config.version;
  if (config.evidence) body.attachment_codes = files.map(file => file.code);
  return body;
}

// A logical request keeps its key until confirmed success, including network retries.
export function requestKey(previous, path, method, body, uuid = () => crypto.randomUUID()) {
  const signature = JSON.stringify([path, method, body]);
  return previous?.signature === signature ? previous : { signature, key: uuid() };
}
