# FE2 Dashboard và CSV (ADM-05/06)

Mở `/app/dashboard`. Thẻ chỉ số phụ thuộc vai trò. MGR xem đầy đủ; SAL xem yêu cầu/đơn/công nợ; DSP xem vận tải và xe; WHS xem lô còn hàng; FIN xem đơn/công nợ/thu tiền. API cũng kiểm quyền từng chỉ số và từng loại CSV.

Khoảng ngày áp dụng cho phát sinh: yêu cầu mới theo `created_at`, đơn xác nhận theo `created_at`, đơn hoàn tất theo audit `ORDER_COMPLETED.occurred_at`, thu ròng theo `payments.occurred_at`. Ngày kết thúc bao gồm hết ngày theo `Asia/Ho_Chi_Minh`. Các thẻ xe, lô, trạng thái đơn và dư nợ là snapshot lúc xem, không bị lọc bởi ngày phát sinh.

Bấm thẻ để mở danh sách chi tiết cùng truy vấn và điều kiện. Backend tính tổng trên toàn bộ tập dữ liệu trước phân trang; tiền thu ròng trừ bản đảo, dư nợ chỉ tính phụ phí đã duyệt và loại đơn hủy. Lô còn hàng đếm theo lô, không cộng kiện với pallet. Xe đang sử dụng đếm từng xe một lần.

Xuất CSV đơn hàng, lịch xe, tồn kho hoặc công nợ trong đúng quyền. CSV xuất toàn bộ tập lọc, có khoảng ngày và thời điểm trong tên file; các ô có thể bị bảng tính diễn giải thành công thức được làm an toàn. Mỗi lần xuất ghi `REPORT_EXPORT` vào audit. Không xuất token, mật khẩu hoặc dữ liệu khách ngoài quyền.

Kiểm thử mới trong `tests/test_fe2_reports.py`: khớp thẻ/danh sách, ngày cuối, snapshot, phân quyền, CSV và audit. Sau khi thay đổi backend, khởi động lại backend rồi tải lại frontend. Tài liệu API ở `/docs`, nhóm endpoint `/api/fe2/dashboard` và `/api/fe2/reports`.
