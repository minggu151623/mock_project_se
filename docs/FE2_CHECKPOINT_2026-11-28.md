# FE2 checkpoint — 28/11/2026

Nhánh `FE2`. Đây là điểm dừng kỹ thuật trước khi nhóm review/merge. Bản sao lưu cục bộ nằm trong `data/fe2-backups/` (được `.gitignore` bỏ qua); trạng thái commit/PR xem bằng Git.

## Đã kiểm tra

- Vận tải trên UI, DB riêng `data/fe2-uat-final.sqlite3`: `TRP-000006` đi từ chưa phân công → phân công → nhận hàng → đang vận chuyển → giao hàng có tệp → hoàn tất. Đơn `ORD-000005` thành `COMPLETED`; nút ghi tiếp không còn.
- Thuê xe trên UI: `RNT-000005` giữ xe → bàn giao có ảnh/km 1000 → trả xe có ảnh/km 1100 → hoàn tất. Đơn `ORD-000007` thành `COMPLETED`. Kịch bản `RNT-000003` trả muộn 744 phút: trả xe có ảnh, phụ phí 300.000 ₫ chờ duyệt làm ẩn nút hoàn tất, MGR duyệt qua API, UI hoàn tất, FIN thu đủ 2.500.000 ₫.
- Kho trên UI: `LOT-000005` nhập 10 THUNG → xác nhận → đề nghị điều chỉnh giảm 1 → `mgr1` duyệt → tồn 9 → thử đóng sớm bị `LOT_NOT_CLOSABLE` → xuất 9 và liên kết `TRP-000004` → tồn 0 → đóng lô. Đơn `ORD-000008` thành `COMPLETED`.
- Thanh toán: `fin1` ghi thu 100.000 VND cho `ORD-000008`, rồi đảo `PAY-000005`; số dư quay lại 1.100.000 VND. Dashboard FIN hiển thị số dư 13.100.000 VND và danh sách drilldown 8 đơn khớp số; CSV công nợ tải được.
- Vai trò được quan sát: `DSP` có Vận tải/Thuê xe, `WHS` có Kho, `MGR` duyệt điều chỉnh, `FIN` có Thanh toán. API NEG và E2E đã có trong `tests/`.
- Benchmark Dashboard trên 100.000 đơn và 1.000.000 giao dịch kho: 10 mẫu/endpoint; p95 Dashboard FIN 1084,62 ms, Dashboard SAL 1441,04 ms, chi tiết công nợ 287,49 ms, chi tiết quá hạn 137,17 ms, lô còn hàng 108,47 ms. Tất cả dưới ngưỡng đọc 2000 ms. Lệnh: `.\.venv\Scripts\python.exe -m scripts.benchmark_fe2_dashboard`.
- Frontend: 14 test Node đạt, `npm run build` đạt. Toàn bộ backend: **190 pytest đạt** sau khi thêm test Dashboard trả muộn, 1 cảnh báo deprecation của Starlette/httpx.

## Thay đổi mới ở checkpoint này

- API đổi giờ kết thúc dự kiến của chuyến yêu cầu `version`, kiểm tra xung đột và tăng version chuyến.
- Hoàn tất thuê xe, xác nhận phiếu kho, duyệt/từ chối điều chỉnh dùng `idempotent_write`; FE giữ khóa xác nhận khi thử lại.
- Chính sách hiển thị nút Kho/Thanh toán được tách ra và có test role/trạng thái. Form Kho khóa nội dung khi kết quả ghi chưa rõ để bảo toàn cùng payload khi retry.
- DB UAT riêng có thao tác thử nghiệm. `TRP-000004` được đánh dấu là chuyến lấy tại kho cùng khách để kiểm tra liên kết. `RNT-000005` được đổi thời gian yêu cầu trong DB thử nghiệm; thao tác này không thay đổi thời gian kết thúc booking và vì vậy không tạo tình huống trả muộn.
- Dashboard “Xe thuê quá hạn” đã sửa để dùng `resource_bookings.planned_end` như màn Thuê xe, thay vì `rental_records.requested_end`.

## Còn cần làm trước khi chốt 100%

1. Chạy lại NEG/quyền/xung đột sau khi merge với nhánh chủ sở hữu BE2, đặc biệt hợp đồng API `planned-end` mới bắt buộc `version`.
2. FE1 hoàn thiện thao tác đề nghị/duyệt phụ phí trên `/app/phu-phi` nếu nghiệm thu yêu cầu thao tác E2E-02 hoàn toàn bằng UI; phiên này hai bước qua API.
3. Đối chiếu các file yêu cầu nguồn trong `pdf folder into markdown/`, để BE3/nhóm ký nghiệm thu.
4. Review diff với BE2, commit các file FE2 cần thiết, mở PR vào `main`, chạy lại test trên release candidate. Không đưa `frontend/node_modules`, `frontend/dist`, DB UAT hoặc thư mục PDF/Markdown của người dùng vào PR.
