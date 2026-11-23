# Bàn giao BE1 ↔ BE3

Tài liệu này ghi lại trạng thái BE1 và các phần cần BE3 tích hợp/kiểm thử. Phạm vi tài khoản thật, phiên đăng nhập, upload tệp dùng chung và kiểm thử chéo thuộc BE3; BE1 không tạo một nguồn dữ liệu người dùng thứ hai.

## Trạng thái BE1 hiện tại

- Đã triển khai các nghiệp vụ SAL-01→09 và PAY-01→04 trong `backend/sales.py` và `backend/api_sales.py`.
- Đã có migration phiên bản và quy ước cập nhật schema trong `backend/db.py` và `backend/migrations/`.
- Seed mẫu BE1 nằm riêng trong `backend/seed_sales_dev.py`; có thể chạy sau khi DB đã được khởi tạo.
- Test BE1 nằm trong `tests/test_sales.py`, `tests/test_sales_concurrency.py` và `tests/test_sales_e2e.py`.
- Suite hiện tại: 115 test passed. Các luồng tích hợp BE1/BE2 đã bao gồm vận tải, thuê xe trả muộn có phụ phí, và kho nối sang vận tải.
- Nhánh làm việc: `BE1`. Thay đổi chưa commit/push.

## Việc cần BE3 tiếp nối

### 1. Nguồn tài khoản nội bộ và kiểm tra người phụ trách SAL

Repo hiện chưa có tầng tài khoản thật; `backend/auth.py` chỉ có principal giả dùng trong dev. Khi SAL gán người phụ trách ở SAL-03, hiện BE1 chưa thể kiểm tra tài khoản được gán đang `ACTIVE` và có vai trò `SAL`.

BE3 cần cung cấp một hợp đồng dùng chung để BE1 truy vấn trạng thái và role từ nguồn tài khoản chính thức, ví dụ helper/service nhận username và trả về trạng thái cùng danh sách role. Gắn kiểm tra đó vào thao tác gán người phụ trách. Không tạo bảng user riêng trong BE1.

### 2. Nối xác thực phiên thật vào API

BE3 thay `app.state.principal_provider` bằng xác thực phiên thật, trả về `Principal(username, roles)` và giữ tương thích với `require_roles`. Kiểm tra thu hồi role/khóa tài khoản có hiệu lực ngay ở request kế tiếp theo ADM-01/02 và NFR-02.

BE1 hiện kiểm tra role ở endpoint theo role được gửi qua cơ chế dev. Các kiểm tra đó cần được chạy lại bằng principal thật của BE3, đặc biệt là SAL, FIN, MGR, DSP và WHS.

### 3. Thống nhất dịch vụ audit và attachment

- `backend/audit.py` do BE3 sở hữu. Khi thay implementation, giữ chữ ký `write_audit(...)` mà BE1 đang gọi; bảo đảm audit append-only và không ghi token plaintext.
- `backend/attachments.py` do BE3 sở hữu. BE1 dùng `attachments.link(...)` để gắn evidence cho `QuoteResponse` và `Charge`. Kiểm tra luồng phân quyền upload, xem, tải và gắn tệp với các owner type này.
- Phụ phí BE1 yêu cầu ít nhất một attachment hợp lệ. Phản hồi báo giá lưu nội dung trao đổi; evidence tệp là tùy chọn.

### 4. Nối public website và kiểm tra E2E đầu-cuối

BE3 triển khai/nối PUB-02/03/04 với `service_requests` của BE1:

- Tạo request với `source="WEBSITE"`, validation dữ liệu theo dịch vụ và chống gửi trùng/giới hạn gửi theo SRS.
- Sinh token tra cứu đủ độ dài, chỉ trả một lần và lưu hash trong `service_requests.public_token_hash`.
- Tra cứu bằng POST; không đưa token vào URL/log. Chỉ trả các trường được phép theo BR-18, không trả internal notes, event nội bộ, `public_token_hash` hoặc công nợ.
- Khi cần cấp lại token, dùng luồng BE1 `POST /api/sales/requests/{code}/rotate-token` sau khi xác minh thủ công; token cũ bị thay ngay.

Các test E2E BE1/BE2 hiện bắt đầu từ bước tiếp nhận nội bộ. Chúng chưa kiểm thử website công khai, phát token ban đầu, tra cứu công khai hoặc các quyền dữ liệu PUB-04. BE3 cần nối các bước đó và chạy lại E2E-01/02/03 đầy đủ theo tài liệu 08.

### 5. Kiểm thử chéo, bảo mật và nghiệm thu

- Chạy NEG-01→24 và E2E-01→03 trên toàn hệ thống, bổ sung kiểm tra sai quyền/đổi mã đối tượng và bảo vệ token.
- Đo NFR-04 trên dữ liệu mục tiêu; truy vấn công nợ BE1 đã được lọc, phân trang và tổng hợp trong SQL nhưng chưa có báo cáo benchmark 100.000 đơn.
- Rà soát NFR-07/08/09/10/11, UAT và traceability theo tài liệu 08.

## Điểm nối BE1 ↔ BE2 đang dùng

- SAL-06 gọi `assignments.create_transport_trip`, `assignments.create_rental_record` hoặc `warehouse.create_warehouse_lot` trong cùng transaction tạo đơn.
- SAL-09 gọi `assignments.cancel_operational_record(...)` để hủy hồ sơ và giải phóng lịch.
- `sales.bind_integrations()` gán `pending_charge_count`, `operation_started` và `operation_completed` vào `integrations.hooks` khi khởi động app.
- Đơn vận tải lấy hàng từ kho cần `service_data.pickup_from_warehouse=true`; BE1 chuyển cờ này sang hồ sơ chuyến để WHS liên kết phiếu xuất.

## Tài liệu đối chiếu

- `SRS/00_Overall_SRS.pdf`
- `SRS/02_Sales_and_Service_Orders.pdf`
- `SRS/06_Payment_Tracking.pdf`
- `SRS/08_Acceptance_and_Traceability.pdf`
- `AGENTS.md`, `PHAN_CONG.md`, `backend/migrations/README.md`
