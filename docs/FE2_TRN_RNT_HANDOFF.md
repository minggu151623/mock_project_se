# FE2 — Vận tải và Thuê xe

Cập nhật: 28/11/2026. Nhánh làm việc: `FE2`. Tài liệu này mô tả riêng Vận tải và Thuê xe; Kho, Thanh toán, Dashboard/CSV có tài liệu FE2 riêng.

## Phần đã triển khai

| Story | Giao diện |
|---|---|
| TRN-01 | Danh sách, tạo, sửa xe/tài xế; trạng thái nguồn lực; lỗi kèm lịch đang giữ |
| TRN-02 | Lịch theo khoảng ngày, xe/tài xế, lịch giải phóng; mã đơn và loại dịch vụ; phân công, đổi, gỡ phân công |
| TRN-03/04 | Nhận hàng, vận chuyển, kết quả giao hàng, tải bằng chứng, hoàn tất và giải phóng xe |
| TRN-05 | Sự cố, xử lý sự cố, lọc chuyến trễ, cập nhật giờ kết thúc dự kiến |
| RNT-01/02 | Tìm xe trống theo lịch, giữ/đổi/hủy giữ xe |
| RNT-03/04 | Bàn giao/trả xe với kilomet, nhiên liệu, phụ kiện, tình trạng, ảnh hoặc biên bản PDF/JPG/PNG |
| RNT-05 | Hoàn tất, ghi chú bổ sung; xem phụ phí/quyết định và tình trạng thanh toán; ẩn hoàn tất khi biết còn phụ phí chờ duyệt |

Nút thao tác phụ thuộc vai trò/trạng thái. Form gửi version khi API hỗ trợ; giữ nguyên Idempotency-Key cho cùng thao tác thử lại. Khi xung đột version, tải lại bản ghi và yêu cầu mở lại form. Khi mất kết nối ở API chưa hỗ trợ chống trùng, không tự gửi lại mà yêu cầu kiểm tra trạng thái.

## File chính

- `frontend/src/fe2/OperationsPage.jsx`: danh sách, lịch, chi tiết, thông tin đơn/phụ phí/thanh toán.
- `frontend/src/fe2/OperationForm.jsx`: form, bằng chứng, tìm xe, xử lý lỗi và thử lại.
- `frontend/src/fe2/operations.js`: trạng thái, quyền thao tác, payload, thời gian và khóa chống trùng.
- `frontend/src/fe2/operations.css`: bố cục vận hành.
- `backend/api_fe2.py`, `backend/fe2_queries.py`: API chỉ đọc cho lịch và tổng hợp đơn. Tái sử dụng quy tắc lịch và cách tính công nợ hiện có.
- `tests/test_fe2_read.py`, `frontend/src/fe2/operations.test.js`: kiểm thử bổ sung.

## Kiểm tra đã thực hiện

- Toàn bộ backend: 179 test đạt; có một cảnh báo deprecation của thư viện Starlette/httpx.
- 10 test Node: quyền theo vai trò/trạng thái, version, payload, khóa thử lại, giờ Việt Nam giữ chính xác giây và tham số URL sai.
- 18 test API FE2: số tiền chỉ tính phụ phí được duyệt, quyền DSP/SAL/MGR, từ chối ADM/WHS/FIN, yêu cầu đăng nhập, dữ liệu mẫu không có đơn, lịch/lọc/phân trang/giải phóng. Không mở rộng quyền của API Sales hiện có.
- Frontend production build thành công.
- Phiên kiểm tra trước: luồng vận tải đến hoàn tất với PNG; luồng thuê xe giữ xe → bàn giao → trả xe → hoàn tất với PNG, trên database riêng `data/fe2-ui.sqlite3`.
- Phiên này: đăng nhập DSP, kiểm tra hồ sơ RNT-000003 hiện giá và còn phải thu 2.200.000 VND; lịch khoảng ngày hiện cả vận tải/thuê xe và mã đơn. Database riêng `data/fe2-review.sqlite3`.
- Chưa coi đây là nghiệm thu UAT đầy đủ: cần BE3/người dùng kiểm tra chéo toàn bộ kịch bản ngoại lệ và vai trò trong tài liệu 08.

## Điểm cần phối hợp BE2

API cập nhật kết thúc dự kiến hiện bắt buộc `version`, kiểm tra và tăng version. API hoàn tất thuê xe hiện dùng `idempotent_write`. Các thay đổi nằm trên nhánh FE2 và cần BE2 review trước merge. Xem kết quả kiểm thử mới nhất ở `FE2_UAT_HANDOFF.md`.

## Chạy và xem

Từ thư mục `mock_project_se`, mở hai terminal PowerShell:

```powershell
.\scripts\run_fe2.ps1 -Service backend
```

```powershell
.\scripts\run_fe2.ps1 -Service frontend
```

Khởi động lại backend đang chạy để nạp router FE2 mới. Mở `http://127.0.0.1:5174/app/van-tai` hoặc `/app/thue-xe`; API docs tại `http://127.0.0.1:8766/docs`. Dùng tài khoản mẫu DSP đã được seed. Chỉ chạy setup/seed nếu chưa có dữ liệu mẫu cần thiết.

Kiểm tra lại:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd frontend
node --test src/fe2/operations.test.js
npm run build
```
