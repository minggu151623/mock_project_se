const API_ROOT = import.meta.env.VITE_API_BASE_URL || '/api';

export async function api(path, { method = 'GET', body, headers = {}, ...options } = {}) {
  const response = await fetch(`${API_ROOT}${path}`, {
    method, credentials: 'include', headers: { ...(body ? { 'Content-Type': 'application/json' } : {}), ...headers },
    ...(body ? { body: JSON.stringify(body) } : {}), ...options,
  });
  const type = response.headers.get('content-type') || '';
  const data = type.includes('application/json') ? await response.json() : await response.blob();
  if (!response.ok) {
    const err = new Error(data?.error?.message || `Yêu cầu thất bại (${response.status})`);
    err.code = data?.error?.code; err.details = data?.error?.details; err.status = response.status; throw err;
  }
  return data;
}

export const key = () => crypto.randomUUID();
export async function upload(file) {
  const form = new FormData(); form.append('file', file);
  const response = await fetch(`${API_ROOT}/attachments`, { method: 'POST', credentials: 'include', body: form });
  const data = await response.json();
  if (!response.ok) { const err = new Error(data?.error?.message || `Tải tệp thất bại (${response.status})`); err.code = data?.error?.code; throw err; }
  return data;
}
export const money = value => new Intl.NumberFormat('vi-VN').format(Number(value || 0)) + ' ₫';
export const human = value => ({ TRANSPORT: 'Vận chuyển', RENTAL: 'Thuê xe', WAREHOUSE: 'Kho trung chuyển', CONFIRMED: 'Đã xác nhận', IN_PROGRESS: 'Đang thực hiện', COMPLETED: 'Hoàn tất', CANCELLED: 'Đã hủy', NEW: 'Mới', CLARIFYING: 'Cần làm rõ', QUOTING: 'Đang báo giá', SENT: 'Đã gửi', DRAFT: 'Bản nháp', PENDING: 'Chờ duyệt', APPROVED: 'Đã duyệt', REJECTED: 'Từ chối', ACTIVE: 'Đang hoạt động', INACTIVE: 'Ngừng hoạt động' }[value] || value || '—');
