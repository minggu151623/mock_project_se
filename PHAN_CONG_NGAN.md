Các đầu việc chính

Thứ tự làm: BE1, BE2, BE3 làm xong API trước, sau đó FE1, FE2 dựng giao diện trên API thật.

Giai đoạn 1 – Backend

1. BE1 – Kinh doanh và Tài chính - 
   * API khách hàng, yêu cầu, báo giá, đơn, phụ phí, hủy đơn (SAL-01→09); xác nhận báo giá và tạo đúng một đơn trong một transaction kể cả khi hai người bấm cùng lúc.
   * Công nợ và thu tiền (PAY-01→04): không thu vượt khi ghi đồng thời, mỗi khoản đảo tối đa một lần. Thiết kế DB chung, quy ước migration, chống gửi trùng và optimistic locking.

2. BE2 – Vận hành - Minh
   * Đội xe, tài xế và engine giữ lịch: chống trùng lịch khi hai điều phối giữ cùng lúc; vận tải (TRN-01→05) và thuê xe (RNT-01→05) từ phân công đến nhận hàng, giao hàng, bàn giao, trả xe, hoàn tất.
   * Kho (WHS-01→06): lô hàng, phiếu nhập/xuất từng phần, chống tồn âm khi xuất đồng thời, điều chỉnh có MGR duyệt, liên kết vận tải, đóng lô. Cung cấp hook cho BE1 (phụ phí chờ duyệt, đơn bắt đầu/hoàn tất).

3. BE3 – Khách, Quản trị và Kiểm thử - 
   * API website và tra cứu tiến độ (PUB-01→05): token ≥ 128 bit lưu dạng băm, không nằm trên URL. Đăng nhập, phân quyền RBAC, danh mục, audit chỉ thêm, upload tệp (ADM-01→04).
   * Kiểm thử toàn dự án: kế hoạch và ma trận truy vết, chạy 24 NEG ở mức API ngay khi BE xong, sau đó chạy 3 E2E khi FE xong; kiểm thử bảo mật/NFR, đo hiệu năng NFR-04, báo cáo UAT.

Giai đoạn 2 – Frontend (bắt đầu khi API của giai đoạn 1 đã xong)

4. FE1 – Giao diện khách, quản trị, kinh doanh - 
   * Website công khai (PUB-01→05) chạy tốt ở 390px; cổng nội bộ cho quản trị (ADM-01→04) và kinh doanh (SAL-01→09): hồ sơ khách, yêu cầu, báo giá nhiều phiên bản, đơn, phụ phí, hủy đơn.
   * Làm bộ component chung đầu tiên để FE2 dùng lại; CI/CD, deploy và thử sao lưu/khôi phục.

5. FE2 – Giao diện vận hành - 
   * Màn vận tải (TRN-01→05), thuê xe (RNT-01→05), kho (WHS-01→06) và thanh toán (PAY-01→04); chỉ hiện thao tác hợp lệ theo trạng thái hiện tại.
   * Dashboard vận hành và tài chính, xuất CSV (ADM-05/06), làm cả phần truy vấn phía BE; số trên dashboard phải khớp danh sách chi tiết. Nạp dữ liệu UAT.

6. Việc chung - Cả nhóm
   * Cuối giai đoạn 1, ba BE bàn giao tài liệu API (OpenAPI tại /docs) và dữ liệu mẫu, có sẵn đơn CONFIRMED cho cả 3 dịch vụ, để FE bắt đầu.
   * Mỗi người tự viết unit test cho code của mình và sửa bug BE3 báo. Cặp làm việc ở giai đoạn 2: FE1 với BE1, BE3; FE2 với BE2, BE1.
