# FE2 Thanh toán (PAY-01–04)

Trang `/app/thanh-toan` dùng API `GET /api/sales/orders` và chi tiết đơn để hiển thị giá chốt, phụ phí đã duyệt, phải thu, đã thu, còn lại và quá hạn. Bộ lọc gồm mã đơn, khách, dịch vụ, trạng thái thanh toán, ngày đến hạn và chỉ quá hạn; tổng được BE tính trên toàn bộ tập lọc, không theo một trang.

Trong chi tiết, `FIN` ghi thu tiền mặt/chuyển khoản (chuyển khoản cần tham chiếu), xem lịch sử, và đảo toàn bộ khoản thu gốc chưa đảo với lý do. `FIN` hoặc `SAL` có thể đổi ngày đến hạn kèm `version` và lý do. `SAL`, `MGR` chỉ đọc dữ liệu thanh toán. Trang không ghi thu cho đơn hủy hoặc số dư bằng 0. Khoản đảo hiển thị liên kết với mã khoản gốc; thao tác đảo ở đây không chuyển tiền qua ngân hàng.

Form ghi thu và đảo gửi `Idempotency-Key`; khi lỗi mạng/5xx, nội dung bị khóa để thử lại đúng cùng khóa. Lỗi API, kể cả tham chiếu chuyển khoản trùng, được hiển thị. Đổi ngày đến hạn gửi `version` và yêu cầu tải lại khi xung đột.

Kiểm tra nghiệp vụ backend có trong `tests/test_sales.py`, `tests/test_sales_concurrency.py`, `tests/test_neg_all_24.py` và các luồng E2E; kiểm tra build frontend và test FE2 trước khi bàn giao. UAT trực quan trên các tài khoản FIN/SAL/MGR vẫn cần thực hiện trên môi trường nhóm.

Mở `http://localhost:5174/app/thanh-toan`, đăng nhập `fin1` / `Password@123`. Chọn đơn còn nợ → Chi tiết → Ghi nhận thu tiền. Sau khi ghi, kiểm tra số còn lại và thử Đảo khoản thu trên chính dòng lịch sử đó. Dùng `sal1` hoặc `mgr1` để kiểm tra quyền chỉ xem; `sal1` được đổi ngày đến hạn.
