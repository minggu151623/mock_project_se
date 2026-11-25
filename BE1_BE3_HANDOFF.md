# Bàn giao BE1 ↔ BE3

Cập nhật trạng thái sau khi BE3 tích hợp xác thực, website công khai, tệp đính kèm và kiểm thử toàn hệ thống (2026-11-25).

## 1. Tài khoản nội bộ và phân công SAL-03 — hoàn thành

- BE3 sở hữu nguồn tài khoản và vai trò dùng chung; BE1 không tạo bảng người dùng riêng.
- `backend/auth.py` cung cấp hợp đồng kiểm tra tài khoản đang `ACTIVE` và có vai trò `SAL`.
- `backend/sales.py` gọi kiểm tra này khi gán người phụ trách. Kiểm thử tương ứng nằm trong `tests/test_be3_auth_admin.py::test_sal03_assignee_must_be_active_and_have_sal_role`.

## 2. Xác thực và phân quyền API — hoàn thành

- API nội bộ dùng `Principal(username, roles)` lấy từ phiên đăng nhập; cơ chế header chỉ dùng cho môi trường dev.
- BE1 tiếp tục dùng `require_roles`; BE3 quản lý đăng nhập, khóa tài khoản, thu hồi vai trò và phiên.
- Việc khóa tài khoản hoặc thu hồi vai trò có hiệu lực ở request tiếp theo theo kiểm thử BE3 và NEG-22.

## 3. Audit và tệp bằng chứng — đã tích hợp

- BE1 gọi `audit.write_audit(...)`; audit append-only và che dữ liệu nhạy cảm theo kiểm thử BE3.
- BE1 dùng `attachments.link(...)` cho phản hồi báo giá (`QuoteResponse`) và phụ phí (`Charge`).
- Phụ phí cần bằng chứng hợp lệ; tệp bằng chứng phản hồi báo giá là tùy chọn. Phân quyền và kiểm tra tệp thuộc BE3.

## 4. Website công khai và yêu cầu dịch vụ — đã nối

- PUB-02/03 tạo yêu cầu website trong `service_requests`, cấp token tra cứu một lần và chỉ lưu hash.
- PUB-04 tra cứu bằng POST, không đưa token vào URL và chỉ trả các trường công khai được phép theo BR-18.
- Khi cần cấp lại token sau xác minh thủ công, BE1 cung cấp `POST /api/sales/requests/{code}/rotate-token`; token cũ mất hiệu lực.
- Các luồng website → BE1 → BE2 → thanh toán được đưa vào E2E-01→03.

## 5. Kiểm thử và nghiệm thu toàn hệ thống — đã báo cáo hoàn thành

Theo `docs/TEST_PLAN_AND_TRACEABILITY.md` và `docs/UAT_AND_NFR04_REPORT.md`:

- SAL-01→09 và PAY-01→04 được ma trận truy vết đánh dấu `PASS`.
- Báo cáo BE3 ghi nhận 155/155 test, 24/24 NEG, 3/3 E2E; NFR-04 đạt ngưỡng trên 100.000 đơn và 1.000.000 giao dịch kho.
- Các kết quả trên là trạng thái trong tài liệu nghiệm thu; khi thay đổi code, cần chạy lại toàn suite theo hướng dẫn của repo.

## 6. Trạng thái còn lại

- Không còn hạng mục nghiệp vụ BE1 nào được đánh dấu thiếu trong ma trận truy vết hiện tại.
- Review ngày 2026-11-25:
  - BE1 đã sửa 5 lỗi: phát hành báo giá sau khi yêu cầu đã chốt, quyền đề nghị phụ phí theo loại đơn, font PDF tiếng Việt, chi tiết lỗi trùng tham chiếu thanh toán, tên test trùng.
  - BE3 đã sửa 6 mục review.
  - Sau khi gộp nhánh BE1 vào main, toàn bộ suite qua: 161 passed.
- BE1 còn cần thêm test hồi quy riêng cho ba lỗi: phát hành báo giá sau khi chốt, quyền đề nghị phụ phí, chi tiết lỗi trùng tham chiếu.
- FE1 còn giao diện kinh doanh SAL; FE2 còn giao diện thanh toán và dashboard/báo cáo. Đây là việc giai đoạn frontend, không phải phần backend BE1.
- BE1 hỗ trợ FE1/FE2 xử lý lỗi API phát hiện khi tích hợp và cập nhật tài liệu nếu hợp đồng API thay đổi.

## 7. Hợp đồng tích hợp BE1 ↔ BE2

- SAL-06 gọi `assignments.create_transport_trip(...)`, `assignments.create_rental_record(...)` hoặc `warehouse.create_warehouse_lot(...)` trong cùng transaction tạo đơn.
- SAL-09 gọi `assignments.cancel_operational_record(...)` trong cùng transaction hủy đơn để hủy hồ sơ vận hành và giải phóng lịch.
- `sales.bind_integrations()` gắn các hook BE1 vào `integrations.hooks`:
  - `pending_charge_count(conn, order_code) -> int` chặn hoàn tất vận hành khi còn phụ phí `PENDING`.
  - `operation_started(conn, order_code, service_type, at)` cập nhật đơn sang `IN_PROGRESS`.
  - `operation_completed(conn, order_code, service_type, at)` cập nhật đơn sang `COMPLETED`.
- Với vận tải lấy hàng từ kho, BE1 chuyển `service_data.pickup_from_warehouse=true` sang hồ sơ chuyến để BE2 liên kết phiếu xuất.

## 8. Tài liệu tham chiếu

- `docs/TEST_PLAN_AND_TRACEABILITY.md`
- `docs/UAT_AND_NFR04_REPORT.md`
- `AGENTS.md`
- `backend/migrations/README.md`
