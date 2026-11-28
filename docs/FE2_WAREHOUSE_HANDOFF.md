# FE2 Kho (WHS-01–06)

Trang `/app/kho` dành cho WHS thao tác, MGR duyệt điều chỉnh, DSP/SAL chỉ xem. Lô được BE tạo từ đơn kho đã xác nhận.

- WHS-01/04: danh sách lô, tìm/lọc theo mã, khách, trạng thái, ngày nhập; xem dòng hàng, tồn theo từng đơn vị, tuổi lô, ghi chú bảo quản, thêm/sửa dòng trước khi dùng.
- WHS-02/03: lập/sửa/hủy phiếu nhập/xuất nháp; xác nhận phiếu; tồn được kiểm tra lại khi ghi phiếu xuất. Phiếu nhập vượt dự kiến có trường lý do chênh lệch.
- WHS-04: nhật ký phiếu gốc và điều chỉnh, chuyến vận tải liên kết; chọn chuyến cùng khách từ danh sách BE cung cấp.
- WHS-05: WHS đề nghị điều chỉnh từng dòng nguồn, kèm lý do/tệp; MGR xem hàng đợi và duyệt/từ chối.
- WHS-06: WHS xác nhận không còn đợt nhập và đóng lô; lỗi BE nêu tồn, phiếu nháp và phụ phí còn chờ.

Form gửi `version` cho sửa dòng, sửa/hủy phiếu và đóng lô. Phiếu nhập/xuất và đề nghị điều chỉnh dùng cùng `Idempotency-Key` khi thử lại. Giao diện ẩn thao tác theo vai trò, trạng thái; BE kiểm tra lại quyền và tồn trong transaction.

Kiểm tra: frontend build thành công; toàn bộ 179 test backend đạt, trong đó `tests/test_warehouse.py` bao luồng E2E-03 và NEG-12–15. Chưa thực hiện UAT trực quan trên mọi vai trò/trạng thái.

Chạy lại backend/frontend với `scripts/run_fe2.ps1` ở hai terminal, mở `http://localhost:5174/app/kho`. Đăng nhập `whs1` / `Password@123`; dùng `mgr1` để duyệt điều chỉnh.
