# Báo cáo Đo hiệu năng NFR-04, Kiểm thử Bảo mật và Biên bản Nghiệm thu UAT (BE3 – Vũ)

**Dự án:** Hệ thống Quản lý Vận tải hàng hóa, Cho thuê xe tải và Kho trung chuyển  
**Người thực hiện kiểm thử & bàn giao BE3:** Vũ  
**Ngày thực hiện:** 2026-11-25  
**Trạng thái tổng thể:** **ĐẠT (PASSED – 155/155 automated tests, 24/24 NEG, 3/3 E2E, NFR-04 đạt chuẩn)**

---

## 1. Kết quả Đo Hiệu năng NFR-04

### 1.1. Cấu hình Tập dữ liệu Mục tiêu
- **Công cụ chạy:** [scripts/benchmark_nfr04.py](file:///C:/Users/damlo/OneDrive/Documents/School/kì%206/OJT_FPT/Module7/mock_project_se-main/mock_project_se-main/scripts/benchmark_nfr04.py)
- **Quy mô dữ liệu trong DB benchmark:**
  - `100.000` yêu cầu dịch vụ (`service_requests`), `100.000` báo giá (`quotes`), `100.000` đơn dịch vụ (`service_orders`).
  - `1.000.000` giao dịch kho (`warehouse_documents` + `document_lines` ở trạng thái `POSTED` trên `10.000` lô kho).
  - Xác thực qua HTTP API bằng phiên đăng nhập thật (`Authorization: Bearer <token>`).
- **Thời gian nạp dữ liệu benchmark:** `34,40 giây`
- **Số lần đo mỗi thao tác:** `30 lần`

### 1.2. Bảng Kết quả Đo Độ trễ (Latency)

| Nhóm | Thao tác API | Ngưỡng NFR-04 (`p95`) | `p50` (ms) | `p95` (ms) | `p99` (ms) | `Max` (ms) | `Mean` (ms) | Đánh giá |
|---|---|---:|---:|---:|---:|---:|---:|---|
| **ĐỌC** | `GET /api/sales/orders` (lọc & tổng hợp công nợ trên 100.000 đơn) | `<= 2.000 ms` | 170,00 | **198,22** | 199,35 | 199,49 | 172,63 | **ĐẠT** |
| **ĐỌC** | `GET /api/sales/orders/{code}` (chi tiết đơn & số dư trên 100.000 đơn) | `<= 2.000 ms` | 34,42 | **42,36** | 43,98 | 44,19 | 35,22 | **ĐẠT** |
| **ĐỌC** | `POST /api/public/lookup` (tra cứu công khai trên 100.000 yêu cầu) | `<= 2.000 ms` | 24,30 | **28,42** | 37,68 | 41,03 | 25,27 | **ĐẠT** |
| **ĐỌC** | `GET /api/warehouse/lots/{code}` (tra tồn lô trên DB 1.000.000 giao dịch kho) | `<= 2.000 ms` | 39,86 | **43,76** | 51,81 | 54,94 | 40,50 | **ĐẠT** |
| **GHI** | `POST /api/public/requests` (khách gửi yêu cầu mới + băm token) | `<= 3.000 ms` | 31,74 | **35,75** | 45,30 | 49,03 | 32,66 | **ĐẠT** |
| **GHI** | `POST /api/sales/orders/{code}/payments` (ghi nhận thu tiền trên DB 100k đơn) | `<= 3.000 ms` | 42,49 | **46,99** | 48,20 | 48,67 | 42,90 | **ĐẠT** |
| **GHI** | `POST /api/warehouse/lots/{code}/receipts` + `/post` (lập & chốt phiếu nhập trên DB 1M giao dịch) | `<= 3.000 ms` | 289,50 | **331,67** | 344,86 | 349,42 | 294,81 | **ĐẠT** |

---

## 2. Kết quả Kiểm thử Bảo mật và Yêu cầu Phi chức năng (NFR-01 → NFR-12)

| Mã NFR / Quy tắc | Nội dung kiểm tra | Bằng chứng kiểm thử | Kết quả |
|---|---|---|---|
| **NFR-01 / ADM-01** | Băm mật khẩu (`PBKDF2-HMAC-SHA256`), khóa tạm 15 phút sau 5 lần đăng nhập sai trong 15 phút, thông báo lỗi đăng nhập sai đồng nhất giữa tài khoản tồn tại và không tồn tại (`details={}`), hết hạn phiên sau 30 phút không hoạt động, bắt buộc đổi mật khẩu lần đầu | `test_be3_auth_admin.py`, `test_review_be3.py::test_login_error_same_for_existing_and_missing_user` | **ĐẠT** |
| **NFR-02 / ADM-02** | Kiểm tra quyền RBAC ở backend cho mọi API (kể cả GET); `ADM` không mặc nhiên có quyền nghiệp vụ; chỉ `ADM` mới sửa được CMS (`PUB-05`); khóa tài khoản hoặc thu hồi vai trò mất quyền ngay ở request kế tiếp; bảo vệ `ADM` đang hoạt động cuối cùng | `test_be3_auth_admin.py`, `test_review_be3.py::test_cms_write_restricted_to_adm_only`, `test_neg_all_24.py::test_neg_22_*` | **ĐẠT** |
| **NFR-03 / PUB-02,03,04** | Bắt buộc xác nhận sử dụng dữ liệu (`data_use_acknowledged=True`); token tra cứu `>= 128 bit` chỉ trả 1 lần, lưu dạng băm `SHA-256` (không lưu token chữ rõ trong `idempotency_keys`); tra cứu bằng `POST` nhận cả mã yêu cầu (`REQ-...`) lẫn mã đơn (`ORD-...`); không tin `X-Forwarded-For` trừ khi cấu hình `TRUSTED_PROXIES`; giới hạn 5 lần gửi/IP/10 phút và 10 lần tra cứu sai/IP/15 phút | `test_review_be3.py`, `test_be3_public_cms.py`, `test_neg_all_24.py::test_neg_02_*` | **ĐẠT** |
| **NFR-04** | Hiệu năng trên 100.000 đơn & 1.000.000 giao dịch kho: đọc `p95 <= 2s`, ghi `p95 <= 3s` | `scripts/benchmark_nfr04.py` (Đọc `p95` tối đa `198,22 ms`, Ghi `p95` tối đa `331,67 ms`) | **ĐẠT** |
| **NFR-05** | Chống gửi trùng (`Idempotency-Key`) và chống cập nhật cạnh tranh (`version` / `VERSION_CONFLICT` + `BEGIN IMMEDIATE`) | `test_concurrency.py`, `test_sales_concurrency.py`, `test_neg_all_24.py::test_neg_24_*` | **ĐẠT** |
| **NFR-06 / BR-17** | Audit chỉ thêm (append-only) có `before`, `after`, `reason`, tự động che mật khẩu/token, chặn `UPDATE`/`DELETE` bằng trigger DB | `test_db_guards.py::test_audit_log_is_append_only`, `test_be3_auth_admin.py` | **ĐẠT** |
| **NFR-07** | Tải tệp an toàn: kiểm tra magic bytes (`PDF`/`JPG`/`PNG`), `<= 5 MB`, chặn đuôi kép `.exe.png` và script nhúng, lưu tên UUID ngẫu nhiên, trả header `X-Content-Type-Options: nosniff` | `test_trip_ops.py::test_attachment_rules`, `test_neg_all_24.py::test_neg_23_*`, `test_security_nfr.py` | **ĐẠT** |
| **NFR-08 & NFR-10** | Mọi phản hồi có header `X-Request-ID`; lỗi hệ thống 500 trả thông báo tiếng Việt kèm mã tra cứu `X-Request-ID` và tuyệt đối không lộ stack trace hay đường dẫn nội bộ | `test_security_nfr.py::test_request_id_header_and_internal_error_hides_stack_trace` | **ĐẠT** |
| **BR-18** | Tra cứu công khai chỉ hiển thị trạng thái, mốc công khai và thông tin hỗ trợ; không lộ ghi chú nội bộ, số dư công nợ hay thông tin khách hàng riêng tư | `test_be3_public_cms.py::test_public_lookup_post_only_br18_privacy_and_10_failed_attempts_rate_limit` | **ĐẠT** |

---

## 3. Tài khoản và Dữ liệu Mẫu Bàn giao cho FE1, FE2 & UAT

Chạy lệnh sau để khởi tạo toàn bộ dữ liệu mẫu của cả 3 phần (`BE2` + `BE1` + `BE3`) vào `data/dev.sqlite3`:

```bash
.venv/Scripts/python.exe -m backend.seed_be3_dev
```

### 3.1. Danh sách Tài khoản UAT (Mật khẩu mặc định: `Password@123`)
| Tên đăng nhập | Vai trò | Trạng thái | Ghi chú kiểm thử |
|---|---|---|---|
| `adm1` | `ADM` | `ACTIVE` | Quản trị tài khoản, phân quyền, danh mục, CMS, tra cứu audit |
| `sal1`, `sal2` | `SAL` | `ACTIVE` | Quản lý khách hàng, tiếp nhận yêu cầu, lập/phát hành/chốt báo giá, tạo đơn |
| `dsp1`, `dsp2` | `DSP` | `ACTIVE` | Đội xe, tài xế, phân công vận tải, giữ xe thuê, biên bản giao nhận |
| `whs1` | `WHS` | `ACTIVE` | Lô kho, phiếu nhập nhiều đợt, phiếu xuất từng phần, đề nghị điều chỉnh |
| `fin1` | `FIN` | `ACTIVE` | Công nợ phải thu, ghi nhận thanh toán, đảo khoản thu |
| `mgr1` | `MGR` | `ACTIVE` | Duyệt phụ phí, duyệt điều chỉnh kho, hủy đơn hợp lệ, xem báo cáo/audit |
| `firstlogin1` | `SAL` | `ACTIVE` (`must_change_password=1`) | Dùng để test luồng bắt buộc đổi mật khẩu lần đầu (`ADM-01`) |
| `locked1` | `SAL` | `LOCKED` | Dùng để test luồng từ chối tài khoản bị khóa (`ADM-01/02`) |

### 3.2. Dữ liệu Mẫu Đơn hàng & Nội dung
- Đã có sẵn **đơn `CONFIRMED` cho cả 3 dịch vụ** (`TRANSPORT`, `RENTAL`, `WAREHOUSE`) được tạo từ yêu cầu `WEBSITE` kèm token tra cứu công khai.
- Đã có sẵn đội xe (`51C-111.01`..`51D-222.03`), tài xế (`DRV-0001`..`DRV-0004`), lô kho đa đơn vị (`KIEN`, `PALLET`, `THUNG`, `BAO`), danh mục dùng chung và các trang nội dung công khai (`PUBLISHED`, `DRAFT`, `HIDDEN`).

---

## 4. Kết luận Nghiệm thu Giai đoạn Backend
- **Tổng số test tự động toàn dự án:** `155 passed, 0 failed`.
- **24 kịch bản ngoại lệ (`NEG-01→24`):** Đạt `24/24`.
- **3 luồng đầu-cuối (`E2E-01→03`):** Đạt `3/3` (tích hợp đầy đủ từ Website công khai của BE3 → Kinh doanh/Tài chính của BE1 → Vận hành/Kho của BE2).
- **Lỗi nghiêm trọng còn tồn đọng:** `0`.
- **Đủ điều kiện bàn giao OpenAPI (`/docs`) và dữ liệu mẫu cho Giai đoạn 2 (FE1, FE2).**
