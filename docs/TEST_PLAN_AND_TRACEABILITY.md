# Kế hoạch Kiểm thử và Ma trận Truy vết Toàn Dự án (BE3 – Vũ)

Tài liệu này định nghĩa chiến lược kiểm thử, ma trận truy vết (Traceability Matrix) cho toàn bộ **40 User Stories**, **18 Business Rules (BR-01→18)**, **12 Yêu cầu Phi chức năng (NFR-01→12)**, **24 Kịch bản Ngoại lệ (NEG-01→24)** và **3 Luồng Đầu-Cuối (E2E-01→03)** theo tài liệu `00_Overall_SRS` đến `08_Acceptance_and_Traceability`.

---

## 1. Kế hoạch Kiểm thử (Test Plan)

### 1.1. Phạm vi và Mục tiêu
- **Phạm vi:** Toàn bộ hệ thống Backend của 3 thành viên:
  - **BE1 (Kinh doanh & Tài chính):** `SAL-01→09`, `PAY-01→04` ([backend/sales.py](file:///C:/Users/damlo/OneDrive/Documents/School/kì%206/OJT_FPT/Module7/mock_project_se-main/mock_project_se-main/backend/sales.py), [backend/api_sales.py](file:///C:/Users/damlo/OneDrive/Documents/School/kì%206/OJT_FPT/Module7/mock_project_se-main/mock_project_se-main/backend/api_sales.py)).
  - **BE2 (Vận hành – Minh):** `TRN-01→05`, `RNT-01→05`, `WHS-01→06` ([backend/fleet.py](file:///C:/Users/damlo/OneDrive/Documents/School/kì%206/OJT_FPT/Module7/mock_project_se-main/mock_project_se-main/backend/fleet.py), [backend/booking.py](file:///C:/Users/damlo/OneDrive/Documents/School/kì%206/OJT_FPT/Module7/mock_project_se-main/mock_project_se-main/backend/booking.py), [backend/assignments.py](file:///C:/Users/damlo/OneDrive/Documents/School/kì%206/OJT_FPT/Module7/mock_project_se-main/mock_project_se-main/backend/assignments.py), [backend/trip_ops.py](file:///C:/Users/damlo/OneDrive/Documents/School/kì%206/OJT_FPT/Module7/mock_project_se-main/mock_project_se-main/backend/trip_ops.py), [backend/rental_ops.py](file:///C:/Users/damlo/OneDrive/Documents/School/kì%206/OJT_FPT/Module7/mock_project_se-main/mock_project_se-main/backend/rental_ops.py), [backend/warehouse.py](file:///C:/Users/damlo/OneDrive/Documents/School/kì%206/OJT_FPT/Module7/mock_project_se-main/mock_project_se-main/backend/warehouse.py)).
  - **BE3 (Khách, Quản trị & Kiểm thử – Vũ):** `PUB-01→05`, `ADM-01→04`, dịch vụ tệp đính kèm ([backend/auth.py](file:///C:/Users/damlo/OneDrive/Documents/School/kì%206/OJT_FPT/Module7/mock_project_se-main/mock_project_se-main/backend/auth.py), [backend/admin.py](file:///C:/Users/damlo/OneDrive/Documents/School/kì%206/OJT_FPT/Module7/mock_project_se-main/mock_project_se-main/backend/admin.py), [backend/public_site.py](file:///C:/Users/damlo/OneDrive/Documents/School/kì%206/OJT_FPT/Module7/mock_project_se-main/mock_project_se-main/backend/public_site.py), [backend/audit.py](file:///C:/Users/damlo/OneDrive/Documents/School/kì%206/OJT_FPT/Module7/mock_project_se-main/mock_project_se-main/backend/audit.py), [backend/attachments.py](file:///C:/Users/damlo/OneDrive/Documents/School/kì%206/OJT_FPT/Module7/mock_project_se-main/mock_project_se-main/backend/attachments.py)).
- **Mục tiêu chất lượng:**
  1. Mọi quy tắc nghiệp vụ và phân quyền RBAC được kiểm tra chặt chẽ ở Backend và ràng buộc DB (`CHECK`, `UNIQUE`, `TRIGGER`), không phụ thuộc vào Frontend.
  2. Chống thao tác đồng thời (race conditions) trên tạo đơn, giữ lịch xe, xuất kho và thu tiền bằng `BEGIN IMMEDIATE` (`write_tx`).
  3. 100% kịch bản `NEG-01→24` và `E2E-01→03` đạt ở mức API với xác thực phiên thật.
  4. Đạt ngưỡng hiệu năng `NFR-04` trên 100.000 đơn hàng và 1.000.000 giao dịch kho (`p95 <= 2s` đọc, `p95 <= 3s` ghi).

### 1.2. Các Cấp độ Kiểm thử
| Cấp độ | Bộ Test | Công cụ | Mô tả |
|---|---|---|---|
| **1. DB Guards & Unit Tests** | `test_db_guards.py`, `test_booking_engine.py`, `test_sales.py`, `test_warehouse.py` | `pytest`, `sqlite3` | Kiểm tra trực tiếp ràng buộc SQL (`CHECK`, `UNIQUE`, `TRIGGER`) và hàm nghiệp vụ lõi. |
| **2. API & RBAC Integration** | `test_be3_auth_admin.py`, `test_be3_public_cms.py`, `test_fleet_api.py`, `test_assignments_api.py`, `test_trip_ops.py`, `test_rental_ops.py` | `FastAPI TestClient` | Kiểm tra hợp đồng HTTP API, mã lỗi tiếng Việt `{"error": {"code", "message", "details"}}` và phân quyền 6 vai trò (`SAL`, `DSP`, `WHS`, `FIN`, `MGR`, `ADM`). |
| **3. Concurrency Tests** | `test_concurrency.py`, `test_sales_concurrency.py` | `threading.Barrier` | Xuất phát đa luồng đồng thời để kiểm chứng chống giữ đôi xe, chống tạo 2 đơn, chống thu vượt và chống âm tồn kho. |
| **4. 24 Negative Scenarios** | `test_neg_all_24.py` | `pytest` + `TestClient` | Chạy trọn bộ 24 tình huống ngoại lệ `NEG-01` → `NEG-24` ở mức API. |
| **5. 3 End-to-End Flows** | `test_e2e_full_3_flows.py`, `test_sales_e2e.py` | `pytest` + `TestClient` | Chạy 3 luồng đầu-cuối `E2E-01` (Vận tải), `E2E-02` (Thuê xe trả muộn), `E2E-03` (Kho nối vận tải) từ Website công khai qua phiên đăng nhập thật đến Thu tiền và Tra cứu tiến độ. |
| **6. Security & NFR Benchmark** | `test_security_nfr.py`, `scripts/benchmark_nfr04.py` | `pytest`, Python benchmark | Kiểm thử bảo mật (XSS, magic bytes, nosniff, ẩn stack trace, rate limit, token hash) và benchmark `NFR-04`. |

---

## 2. Ma trận Truy vết 40 User Stories (Story → Module → Test Case)

| Mã Story | Tên Story | Phụ trách BE | Module chính | File Test | Trạng thái |
|---|---|---|---|---|---|
| **PUB-01** | Xem thông tin công ty và dịch vụ | BE3 (Vũ) | `public_site.py`, `api_public.py` | `test_be3_public_cms.py::test_cms_lifecycle_html_sanitization_and_protect_all_three_services` | **PASS** |
| **PUB-02** | Gửi yêu cầu dịch vụ từ website | BE3 (Vũ) | `public_site.py`, `api_public.py` | `test_be3_public_cms.py::test_public_request_submission_token_hash_xss_and_rate_limit`, `test_neg_all_24.py::test_neg_01_*` | **PASS** |
| **PUB-03** | Nhận mã và token tra cứu (>= 128 bit) | BE3 (Vũ) | `public_site.py`, `sales.py` | `test_be3_public_cms.py::test_public_request_submission_token_hash_xss_and_rate_limit` | **PASS** |
| **PUB-04** | Tra cứu tiến độ công khai (POST, BR-18) | BE3 (Vũ) | `public_site.py`, `api_public.py` | `test_be3_public_cms.py::test_public_lookup_post_only_br18_privacy_and_10_failed_attempts_rate_limit`, `test_neg_all_24.py::test_neg_02_*` | **PASS** |
| **PUB-05** | Quản lý nội dung công khai (CMS) | BE3 (Vũ) | `public_site.py`, `api_public.py` | `test_be3_public_cms.py::test_cms_lifecycle_html_sanitization_and_protect_all_three_services`, `test_neg_all_24.py::test_neg_21_*` | **PASS** |
| **SAL-01** | Hồ sơ khách hàng & cảnh báo trùng | BE1 | `sales.py`, `api_sales.py` | `test_sales.py::test_duplicate_customer_requires_explicit_reason`, `test_neg_all_24.py::test_neg_03_*` | **PASS** |
| **SAL-02** | Tiếp nhận, làm rõ & cấp lại token | BE1 | `sales.py`, `api_sales.py` | `test_sales.py::test_request_contact_and_token_rotation_are_audited_and_append_only`, `test_neg_all_24.py::test_neg_02_*` | **PASS** |
| **SAL-03** | Phân công SAL ACTIVE & nhật ký trao đổi | BE1 + BE3 | `sales.py`, `auth.py` | `test_be3_auth_admin.py::test_sal03_assignee_must_be_active_and_have_sal_role` | **PASS** |
| **SAL-04** | Lập và kiểm tra báo giá (số nguyên VND) | BE1 | `sales.py`, `sales_pdf.py` | `test_sales.py::test_quote_pdf_is_printable` | **PASS** |
| **SAL-05** | Phiên bản và phản hồi báo giá | BE1 | `sales.py`, `api_sales.py` | `test_sales.py::test_quote_superseded_and_expired_cannot_be_accepted`, `test_neg_all_24.py::test_neg_04_*` | **PASS** |
| **SAL-06** | Xác nhận báo giá và tạo đơn nguyên tử | BE1 + BE2 | `sales.py`, `assignments.py`, `warehouse.py` | `test_sales_concurrency.py::test_only_one_concurrent_quote_acceptance_creates_an_order`, `test_neg_all_24.py::test_neg_05_*` | **PASS** |
| **SAL-07** | Theo dõi và cập nhật đơn (optimistic lock) | BE1 | `sales.py`, `api_sales.py` | `test_sales.py::test_order_note_uses_optimistic_version_and_preserves_snapshots`, `test_neg_all_24.py::test_neg_24_*` | **PASS** |
| **SAL-08** | Ghi nhận và duyệt phụ phí | BE1 | `sales.py`, `api_sales.py` | `test_sales.py::test_charge_proposal_retry_does_not_create_duplicate`, `test_neg_all_24.py::test_neg_16_*` | **PASS** |
| **SAL-09** | Hủy đơn trước thực hiện & giải phóng lịch | BE1 + BE2 | `sales.py`, `assignments.py` | `test_sales.py::test_manager_cancellation_releases_be2_booking_and_preserves_request`, `test_neg_all_24.py::test_neg_19_*` | **PASS** |
| **TRN-01** | Quản lý xe và tài xế | BE2 (Minh) | `fleet.py`, `api.py` | `test_fleet_api.py`, `test_neg_all_24.py::test_neg_08_*` | **PASS** |
| **TRN-02** | Điều phối và đổi phân công chuyến | BE2 (Minh) | `booking.py`, `assignments.py` | `test_booking_engine.py`, `test_assignments_api.py`, `test_neg_all_24.py::test_neg_06_*`, `test_neg_07_*`, `test_neg_11_*` | **PASS** |
| **TRN-03** | Ghi nhận nhận hàng và đang vận chuyển | BE2 (Minh) | `trip_ops.py`, `api_operations.py` | `test_trip_ops.py`, `test_neg_all_24.py::test_neg_09_*`, `test_neg_10_*` | **PASS** |
| **TRN-04** | Xác nhận giao hàng kèm bằng chứng & hoàn tất | BE2 (Minh) | `trip_ops.py`, `attachments.py` | `test_trip_ops.py::test_full_trip_flow`, `test_neg_all_24.py::test_neg_12_*` | **PASS** |
| **TRN-05** | Sự cố và chậm tiến độ | BE2 (Minh) | `trip_ops.py`, `booking.py` | `test_trip_ops.py::test_incident_lifecycle_and_overdue_flag` | **PASS** |
| **RNT-01** | Tra cứu và giữ xe thuê | BE2 (Minh) | `fleet.py`, `assignments.py` | `test_assignments_api.py::test_rental_reserve_change_and_release` | **PASS** |
| **RNT-02** | Đổi hoặc bỏ giữ xe thuê | BE2 (Minh) | `assignments.py`, `api.py` | `test_assignments_api.py::test_rental_reserve_change_and_release` | **PASS** |
| **RNT-03** | Lập biên bản bàn giao xe | BE2 (Minh) | `rental_ops.py`, `api_operations.py` | `test_rental_ops.py::test_full_rental_flow_and_late_minutes` | **PASS** |
| **RNT-04** | Ghi nhận trả xe & tính số phút trễ | BE2 (Minh) | `rental_ops.py`, `api_operations.py` | `test_rental_ops.py`, `test_neg_all_24.py::test_neg_13_*` | **PASS** |
| **RNT-05** | Hoàn tất hồ sơ thuê xe | BE2 (Minh) | `rental_ops.py`, `integrations.py` | `test_rental_ops.py::test_completion_blocked_without_both_inspections_or_with_pending_charges` | **PASS** |
| **WHS-01** | Tạo lô hàng và khóa đơn vị khi đã giao dịch | BE2 (Minh) | `warehouse.py`, `api_warehouse.py` | `test_warehouse.py::test_lot_and_line_rules` | **PASS** |
| **WHS-02** | Lập và xác nhận phiếu nhập nhiều đợt | BE2 (Minh) | `warehouse.py`, `api_warehouse.py` | `test_warehouse.py::test_receipt_batches_and_discrepancy_reason` | **PASS** |
| **WHS-03** | Phiếu xuất từng phần & chống tồn âm đồng thời | BE2 (Minh) | `warehouse.py`, `api_warehouse.py` | `test_warehouse.py::test_concurrent_issues_cannot_overdraw_stock`, `test_neg_all_24.py::test_neg_14_*` | **PASS** |
| **WHS-04** | Tra cứu tồn theo đơn vị & liên kết vận tải | BE2 (Minh) | `warehouse.py`, `api_warehouse.py` | `test_warehouse.py::test_issue_links_only_eligible_transport_trip` | **PASS** |
| **WHS-05** | Điều chỉnh chứng từ đã xác nhận (MGR duyệt) | BE2 (Minh) | `warehouse.py`, `api_warehouse.py` | `test_warehouse.py::test_adjustment_approval_and_source_cap`, `test_neg_all_24.py::test_neg_15_*` | **PASS** |
| **WHS-06** | Kết thúc dịch vụ kho | BE2 (Minh) | `warehouse.py`, `api_warehouse.py` | `test_warehouse.py::test_close_lot_checks_all_blockers` | **PASS** |
| **PAY-01** | Xác định khoản phải thu (BR-13) | BE1 | `sales.py`, `api_sales.py` | `test_sales.py::test_order_payment_reversal_charge_and_receivable_consistency` | **PASS** |
| **PAY-02** | Ghi nhận thu tiền & chống thu vượt đồng thời | BE1 | `sales.py`, `api_sales.py` | `test_sales_concurrency.py::test_only_one_concurrent_payment_can_consume_remaining_balance`, `test_neg_all_24.py::test_neg_17_*` | **PASS** |
| **PAY-03** | Đảo khoản thu nhập sai (tối đa 1 lần) | BE1 | `sales.py`, `api_sales.py` | `test_sales_concurrency.py::test_only_one_concurrent_reversal_is_created`, `test_neg_all_24.py::test_neg_18_*` | **PASS** |
| **PAY-04** | Tra cứu công nợ, lọc & đánh dấu quá hạn | BE1 | `sales.py`, `api_sales.py` | `test_sales.py::test_overdue_filter_counts_and_aggregates_orders_once`, `test_neg_all_24.py::test_neg_20_*` | **PASS** |
| **ADM-01** | Đăng nhập, khóa 5 lần sai/15m, timeout 30m | BE3 (Vũ) | `admin.py`, `auth.py`, `api_admin.py` | `test_be3_auth_admin.py::test_login_session_idle_timeout_and_logout`, `test_five_failed_logins_lock_account_for_15_minutes`, `test_first_login_requires_password_change` | **PASS** |
| **ADM-02** | Tài khoản, phân quyền RBAC & bảo vệ ADM cuối | BE3 (Vũ) | `admin.py`, `auth.py`, `api_admin.py` | `test_be3_auth_admin.py::test_lock_account_and_revoke_role_take_effect_on_next_request_and_protect_last_admin`, `test_neg_all_24.py::test_neg_22_*` | **PASS** |
| **ADM-03** | Danh mục dùng chung (ngừng dùng thay vì xóa) | BE3 (Vũ) | `admin.py`, `api_admin.py` | `test_be3_auth_admin.py::test_shared_catalogs_deactivate_instead_of_delete_and_audit_search` | **PASS** |
| **ADM-04** | Tra cứu lịch sử thao tác (Audit chỉ thêm) | BE3 (Vũ) | `audit.py`, `api_admin.py` | `test_be3_auth_admin.py::test_shared_catalogs_deactivate_instead_of_delete_and_audit_search`, `test_db_guards.py::test_audit_log_is_append_only` | **PASS** |
| **ADM-05** | Dashboard vận hành và tài chính | FE2 | Dữ liệu tổng hợp từ `sales.list_orders`, `trip_ops.list_trips`, `rental_ops.list_rentals`, `warehouse.list_lots` | `test_sales.py::test_due_date_filters_and_summary_cover_all_filtered_pages` | **Sẵn sàng API cho Giai đoạn 2** |
| **ADM-06** | Xuất báo cáo CSV *(Should)* | FE2 | Chờ FE2 ở Giai đoạn 2 | — | **Giai đoạn 2** |

---

## 3. Ma trận 24 Kịch bản Ngoại lệ (NEG-01 → NEG-24)

| Mã NEG | Mô tả tình huống ngoại lệ | Kết quả kỳ vọng | Hàm Test trong `tests/test_neg_all_24.py` | Kết quả |
|---|---|---|---|---|
| **NEG-01** | Gửi yêu cầu dịch vụ từ website thiếu trường bắt buộc theo loại dịch vụ hoặc sai logic thời gian | HTTP 422 `VALIDATION_ERROR`, chỉ rõ `details.field` | `test_neg_01_public_request_missing_or_invalid_service_fields` | **PASS** |
| **NEG-02** | Tra cứu công khai với token sai hoặc token cũ đã bị cấp lại (`/rotate-token`) | HTTP 401 `INVALID_LOOKUP_CREDENTIALS`, token mới tra cứu thành công | `test_neg_02_public_lookup_wrong_or_rotated_token_and_rate_limit` | **PASS** |
| **NEG-03** | Tạo khách hàng trùng số điện thoại/email với khách hàng ACTIVE mà không có lý do | HTTP 409 `CUSTOMER_DUPLICATE`, liệt kê `details.matches` | `test_neg_03_duplicate_customer_contact_without_reason` | **PASS** |
| **NEG-04** | Chấp thuận báo giá đã `SUPERSEDED` (bị thay thế) hoặc đã `EXPIRED` (quá hạn) | HTTP 409 `INVALID_STATE` / `QUOTE_EXPIRED` | `test_neg_04_accept_superseded_or_expired_quote` | **PASS** |
| **NEG-05** | Hai nhân viên kinh doanh cùng bấm chấp thuận báo giá tạo đơn đồng thời | Chỉ 1 luồng thành công tạo 1 đơn; luồng kia nhận `INVALID_STATE` | `test_neg_05_concurrent_quote_acceptance_creates_single_order` | **PASS** |
| **NEG-06** | Phân công xe/tài xế trùng khung giờ `[start, end)` với lịch đã giữ | HTTP 409 `BOOKING_CONFLICT` kèm danh sách xung đột | `test_neg_06_vehicle_and_driver_schedule_overlap` | **PASS** |
| **NEG-07** | Hai điều phối viên giữ cùng một xe đồng thời cho 2 chuyến trùng giờ | Chỉ 1 người giữ được xe; người kia nhận `BOOKING_CONFLICT` | `test_neg_07_concurrent_vehicle_booking_race` | **PASS** |
| **NEG-08** | Ngừng hoạt động/bảo dưỡng xe hoặc tài xế khi đang chạy hoặc còn lịch tương lai | HTTP 409 `FUTURE_BOOKINGS_EXIST` / `RESOURCE_IN_USE` | `test_neg_08_deactivate_vehicle_or_driver_with_active_or_future_bookings` | **PASS** |
| **NEG-09** | Phân công xe không đủ tải trọng, xe không ACTIVE, hoặc nhận hàng thực tế vượt tải | HTTP 409 `CAPACITY_INSUFFICIENT` / `VEHICLE_NOT_ELIGIBLE` / `OVER_CAPACITY` | `test_neg_09_ineligible_vehicle_or_insufficient_capacity_or_overweight_pickup` | **PASS** |
| **NEG-10** | Nhận hàng/bàn giao xe khi xe chưa xong việc trước hoặc ngoài khung giờ đã giữ | HTTP 409 `OUTSIDE_BOOKED_WINDOW` / `RESOURCE_STILL_IN_USE` | `test_neg_10_start_operation_while_resource_still_in_use_or_outside_window` | **PASS** |
| **NEG-11** | Đổi phân công xe/lịch sang khung giờ xung đột thất bại | HTTP 409 `BOOKING_CONFLICT`, giữ nguyên phân công cũ | `test_neg_11_failed_reassignment_preserves_existing_booking` | **PASS** |
| **NEG-12** | Xác nhận giao hàng thiếu tệp bằng chứng hoặc bỏ bước trạng thái chuyến | HTTP 409 `INVALID_STATE` / HTTP 422 `VALIDATION_ERROR` | `test_neg_12_delivery_without_evidence_or_invalid_trip_state_transition` | **PASS** |
| **NEG-13** | Trả xe thuê có số km nhận lại < số km bàn giao, hoặc đóng hồ sơ khi thiếu biên bản | HTTP 422 `odometer_km` / HTTP 409 `EVIDENCE_REQUIRED` | `test_neg_13_rental_return_odometer_less_than_handover_or_incomplete_close` | **PASS** |
| **NEG-14** | Hai thủ kho cùng xác nhận phiếu xuất vượt tồn khả dụng đồng thời | Chỉ 1 phiếu thành công; phiếu kia nhận `INSUFFICIENT_STOCK`, tồn không âm | `test_neg_14_concurrent_warehouse_issue_prevents_negative_stock` | **PASS** |
| **NEG-15** | Điều chỉnh chứng từ kho vượt số lượng nguồn hoặc đóng lô sớm khi còn tồn | HTTP 409 `ADJUSTMENT_EXCEEDS_SOURCE` / `LOT_NOT_CLOSABLE` | `test_neg_15_warehouse_adjustment_exceeds_source_or_premature_lot_close` | **PASS** |
| **NEG-16** | Đề nghị phụ phí thiếu bằng chứng, trên đơn đã đóng, hoặc duyệt lặp phụ phí | HTTP 422 / HTTP 409 `INVALID_STATE` | `test_neg_16_charge_proposal_without_evidence_or_on_closed_order_or_double_decision` | **PASS** |
| **NEG-17** | Ghi nhận thanh toán vượt số dư còn phải thu hoặc trùng tham chiếu chuyển khoản | HTTP 409 `PAYMENT_EXCEEDS_BALANCE` / `PAYMENT_REFERENCE_EXISTS` | `test_neg_17_overpayment_concurrent_payment_and_duplicate_transfer_reference` | **PASS** |
| **NEG-18** | Đảo khoản thu lần thứ hai hoặc đảo chính bản ghi đảo | HTTP 409 `INVALID_STATE` / `PAYMENT_ALREADY_REVERSED` | `test_neg_18_reverse_payment_twice_or_reverse_reversal_entry` | **PASS** |
| **NEG-19** | Hủy đơn khi đơn đã bắt đầu thực hiện, đã có khoản thu hiệu lực hoặc còn phụ phí | HTTP 409 `NOT_CANCELLABLE` / `CHARGES_EXIST` | `test_neg_19_cancel_order_after_execution_started_or_with_payments_or_charges` | **PASS** |
| **NEG-20** | Ranh giới ngày quá hạn thanh toán và tổng hợp công nợ đơn có nhiều phụ phí/thanh toán | Đúng ngày hạn chưa quá hạn; qua ngày sau quá hạn; tổng tiền không đếm đôi | `test_neg_20_overdue_receivable_boundary_and_no_double_counting` | **PASS** |
| **NEG-21** | Khách đọc bài CMS đang `DRAFT`/`HIDDEN` hoặc quản trị cố ẩn cả 3 trang dịch vụ | HTTP 404 `NOT_FOUND` / HTTP 409 `ALL_SERVICES_HIDDEN` | `test_neg_21_public_access_to_draft_or_hidden_cms_and_hide_all_services` | **PASS** |
| **NEG-22** | Gọi API không đăng nhập, sai vai trò (kể cả ADM gọi API nghiệp vụ), khóa phiên sau 5 lần sai | HTTP 401 `UNAUTHORIZED` / HTTP 403 `FORBIDDEN` / `ACCOUNT_LOCKED` | `test_neg_22_rbac_unauthorized_roles_login_lockout_and_immediate_revocation` | **PASS** |
| **NEG-23** | Upload tệp sai magic bytes, đuôi kép `.exe.png`, chứa `<script>`, > 5MB, hoặc gắn lại tệp đã dùng | HTTP 422 `VALIDATION_ERROR` / HTTP 409 `ATTACHMENT_ALREADY_USED` | `test_neg_23_upload_invalid_file_type_size_script_or_reuse_linked_attachment` | **PASS** |
| **NEG-24** | Cập nhật hồ sơ với `version` cũ hoặc dùng lại `Idempotency-Key` cho thao tác khác | HTTP 409 `VERSION_CONFLICT` / `IDEMPOTENCY_KEY_REUSED` | `test_neg_24_optimistic_locking_version_conflict_and_idempotency_key_reuse` | **PASS** |

---

## 4. Ma trận 3 Luồng Đầu-Cuối (E2E-01 → E2E-03)

| Mã E2E | Luồng nghiệp vụ đầu-cuối | Các vai trò tham gia | Hàm Test trong `tests/test_e2e_full_3_flows.py` | Kết quả |
|---|---|---|---|---|
| **E2E-01** | **Vận tải hàng hóa:** Khách gửi form Website → Nhận mã & token → SAL đăng nhập làm rõ, tạo khách, lập & phát hành báo giá → Khách chấp thuận tạo đơn → DSP phân công xe/tài xế, nhận hàng, vận chuyển, tải bằng chứng giao hàng, hoàn tất chuyến → FIN thu tiền 2 đợt (`PARTIALLY_PAID` → `PAID`) → Khách tra cứu tiến độ công khai thấy đủ 4 mốc, không lộ công nợ. | Khách, `SAL`, `DSP`, `FIN` | `test_e2e_01_transport_from_public_website_to_delivery_and_two_payments` | **PASS** |
| **E2E-02** | **Cho thuê xe trả muộn có phụ phí:** Khách gửi form thuê xe Website → SAL báo giá & lên đơn → DSP giữ xe, lập biên bản bàn giao có ảnh, nhận lại xe muộn hôm sau (ghi nhận 1.500 phút trễ), đề nghị phụ phí kèm bằng chứng (khi chưa duyệt bị chặn đóng hồ sơ) → MGR duyệt phụ phí → DSP hoàn tất hồ sơ thuê → FIN thu đủ tiền gốc + phụ phí → Khách tra cứu tiến độ công khai. | Khách, `SAL`, `DSP`, `MGR`, `FIN` | `test_e2e_02_rental_late_return_surcharge_approval_and_settlement` | **PASS** |
| **E2E-03** | **Kho trung chuyển nối sang Vận tải:** Khách gửi yêu cầu kho Website → SAL lên đơn kho → WHS nhập kho 2 đợt (60 + 40 = 100 kiện) → Khách gửi yêu cầu vận tải lấy từ kho → SAL lên đơn vận tải → WHS xuất đợt 1 (30 kiện), xuất đợt 2 (70 kiện) liên kết chuyến vận tải → Tồn về 0, WHS đóng lô kho → DSP chạy và hoàn tất chuyến vận tải → FIN đối chiếu và thu tiền riêng từng đơn → Khách tra cứu công khai cả 2 mã đều `COMPLETED`. | Khách, `SAL`, `WHS`, `DSP`, `FIN` | `test_e2e_03_warehouse_partial_issues_linked_to_transport_and_separate_receivables` | **PASS** |
