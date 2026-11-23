# Hướng dẫn cho AI agent

Tài liệu này dành cho AI agent (Claude Code, Cursor, Codex…) đang giúp một thành viên làm mock project. Đọc hết trước khi viết code.

**Việc đầu tiên:** hỏi người dùng họ phụ trách phần nào (BE1, BE3, FE1 hay FE2), nếu họ chưa nói. Chỉ làm trong phạm vi phần đó.

## 1. Project

Hệ thống quản lý vận tải hàng hóa, cho thuê xe tải và kho trung chuyển, gồm:
- website công khai cho khách (không cần đăng nhập);
- cổng nội bộ cho nhân viên theo vai trò.

Yêu cầu nằm trong 9 file PDF. Repo để công khai nên **không chứa các PDF này** (`*.pdf` đã nằm trong `.gitignore`). Mỗi thành viên tự chép 9 file vào thư mục gốc của repo trên máy mình:

| File | Nội dung |
|---|---|
| `00_Overall_SRS.pdf` | Phạm vi, vai trò, business rule BR-xx, NFR-xx, ngoài phạm vi |
| `01_Public_Website.pdf` | PUB-01→05 |
| `02_Sales_and_Service_Orders.pdf` | SAL-01→09 |
| `03_Freight_Transportation.pdf` | TRN-01→05 |
| `04_Vehicle_Rental.pdf` | RNT-01→05 |
| `05_Transshipment_Warehouse.pdf` | WHS-01→06 |
| `06_Payment_Tracking.pdf` | PAY-01→04 |
| `07_Administration_and_Reporting.pdf` | ADM-01→06 |
| `08_Acceptance_and_Traceability.pdf` | Kịch bản E2E-01→03, test ngoại lệ NEG-01→24, dữ liệu UAT |

Luôn đọc `00` và `08`, cộng với file của các story mình làm. Quy tắc trong PDF là nguồn sự thật. Gặp chỗ mơ hồ thì hỏi người dùng, không tự đoán.

Bảng phân công chi tiết: `PHAN_CONG.md`. Bản ngắn: `PHAN_CONG_NGAN.md`.

## 2. Phân công và thứ tự

**Giai đoạn 1, Backend làm trước.** BE1, BE2, BE3 làm xong API.
**Giai đoạn 2, Frontend làm sau.** FE1, FE2 dựng giao diện trên API thật.

| Phần | Người | Story | Trạng thái |
|---|---|---|---|
| BE1: Kinh doanh và Tài chính | | SAL-01→09, PAY-01→04; DB chung, quy ước migration, chống gửi trùng, optimistic locking | Chưa làm |
| BE2: Vận hành | Minh | TRN-01→05, RNT-01→05, WHS-01→06 | **Xong, 89 test qua** |
| BE3: Khách, Quản trị, Kiểm thử | | PUB-01→05, ADM-01→04, upload tệp; kiểm thử toàn dự án (24 NEG, 3 E2E, bảo mật, hiệu năng NFR-04, báo cáo UAT) | Chưa làm |
| FE1: Giao diện khách, quản trị, kinh doanh | | Màn của PUB, ADM-01→04, SAL; bộ component chung (làm đầu tiên); CI/CD, deploy, sao lưu | Chờ giai đoạn 2 |
| FE2: Giao diện vận hành | | Màn của TRN, RNT, WHS, PAY; dashboard và xuất CSV ADM-05/06 (làm cả truy vấn phía BE); dữ liệu UAT | Chờ giai đoạn 2 |

Mỗi người tự viết unit test cho code của mình. BE3 kiểm thử chéo và báo bug cho người sở hữu code.

## 3. Stack và cách chạy

- **Backend:** Python 3, FastAPI, pydantic v2, SQLite qua `sqlite3` thuần (không ORM), pytest + httpx.
- **Frontend:** Vite + React, đặt trong thư mục `frontend/` (chưa tạo).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/python -m backend.seed_dev
DEV_AUTH=1 .venv/bin/uvicorn backend.main:create_app --factory --reload --port 8765
```

- Tài liệu API: http://localhost:8765/docs
- Khi chạy với `DEV_AUTH=1`, gọi API bằng header `X-Dev-User: dsp1` và `X-Dev-Roles: DSP`. Không bật biến này trên môi trường thật.
- Schema ban đầu ở `backend/schema.sql` được áp dụng như migration phiên bản 1.
  Các thay đổi tiếp theo dùng file tuần tự `backend/migrations/NNNN_*.sql`;
  không sửa migration đã dùng ở máy khác. `backend/db.py:init_db()` ghi lịch sử
  vào `schema_migrations`. Quy tắc chi tiết ở `backend/migrations/README.md`.

## 4. Cấu trúc backend hiện có

| File | Vai trò | Chủ sở hữu |
|---|---|---|
| `main.py` | `create_app()`, xử lý lỗi, X-Request-ID, đăng ký router | Chung. Chỉ thêm `include_router` của mình |
| `schema.sql` | Toàn bộ bảng, trigger, index | Chung. Mỗi người thêm khối bảng của mình, có dòng chú thích tên phần |
| `db.py` | `connect`, `init_db`, `write_tx` (BEGIN IMMEDIATE), `next_code` | Chung |
| `errors.py` | `DomainError` và các lớp `NotFound`, `ValidationFailed`, `Conflict`, `Unauthorized`, `Forbidden` | Chung |
| `timeutil.py`, `validation.py` | Giờ UTC, `clean_text`, `check_version` | Chung |
| `api_common.py` | `get_conn`, `role_dependency`, `Body`, `idempotent_write`, kiểu `ReasonText`, `Limit`… | Chung |
| `auth.py` | `Principal`, `require_roles`, bộ vai trò. Hiện chỉ có đăng nhập giả cho dev | **BE3 thay** bằng phiên thật |
| `audit.py` | `write_audit(...)` bản tối thiểu | **BE3** có thể thay, giữ nguyên chữ ký hàm |
| `attachments.py` | Upload tệp, kiểm tra loại bằng magic bytes, ≤ 5 MB | **BE3** có thể thay, giữ nguyên tên hàm |
| `idempotency.py` | `run_idempotent` bản tối thiểu | **BE1** có thể thay, giữ nguyên chữ ký hàm |
| `integrations.py` | `hooks`: điểm nối BE2 → BE1 | **BE1** gán hàm thật |
| `booking.py`, `fleet.py`, `assignments.py`, `trip_ops.py`, `rental_ops.py`, `warehouse.py`, `api.py`, `api_operations.py`, `api_warehouse.py`, `seed_dev.py` | Toàn bộ BE2 | **BE2 (Minh).** Không sửa nếu chưa hỏi |

Đọc `backend/README.md` để biết quy tắc nghiệp vụ, mã lỗi và vai trò của BE2.

## 5. Quy ước bắt buộc (backend)

Làm theo đúng cách BE2 đã làm, để cả nhóm dùng chung một kiểu. Trước khi viết, mở một file BE2 tương ứng ra xem mẫu.

- **Tách lớp.** Logic nghiệp vụ đặt trong module riêng (ví dụ `backend/quotes.py`). Hàm nhận `conn, ..., actor, now` và trả `dict`. Router đặt ở `backend/api_<phần>.py` và chỉ làm việc parse, kiểm quyền rồi gọi module.
- **Ghi dữ liệu.**
  - Mọi thao tác ghi chạy trong `with write_tx(conn):`.
  - Thao tác xác nhận (tạo đơn, thu tiền…) dùng `idempotent_write(...)` và header `Idempotency-Key`.
  - Kiểm tra rồi ghi phải nằm trong cùng một transaction.
- **Chống ghi đè.** Bảng sửa được có cột `version`. Request sửa phải gửi `version`. Gọi `check_version()`, lệch thì trả `VERSION_CONFLICT` (409).
- **Lỗi.**
  - Ném lớp con của `DomainError`, kèm `code` viết HOA_GẠCH_DƯỚI ổn định và `message` tiếng Việt.
  - Cần thêm dữ liệu cho FE thì đưa vào `details`.
  - Dạng JSON trả về luôn là `{"error": {"code", "message", "details"}}`.
  - Không để lộ stack trace.
- **Quyền.** Mọi endpoint có `Depends(role_dependency(...))`, kể cả GET. Kiểm quyền ở backend, không dựa vào FE ẩn nút. `ADM` không mặc nhiên có quyền nghiệp vụ.
- **Thời gian.**
  - Lưu UTC dạng `2026-12-01T01:00:00Z` qua `to_db()`.
  - Giờ đầu vào không có múi giờ thì hiểu là Asia/Ho_Chi_Minh.
  - Lấy giờ hiện tại từ `request.app.state.clock()` (`now_of`), không gọi `datetime.now()` trong logic, để test điều khiển được.
- **Tiền.** Dùng số nguyên VND, không dùng float.
- **Body request.** Kế thừa `Body` (cấm trường lạ, tự cắt khoảng trắng). Dùng các kiểu `Annotated` trong `api_common.py`, không tạo `Field(...)` hay `Query(...)` dùng chung giữa các endpoint.
- **Mã nghiệp vụ.** Sinh bằng `next_code(conn, "ORD")`, ra dạng `ORD-000001`.
- **Audit.** Mỗi thay đổi quan trọng gọi `write_audit(...)` trong cùng transaction, kèm `before`, `after` và `reason`.
- **Bảo vệ ở DB.** Quy tắc quan trọng, nhất là chống trùng, chống âm và bản ghi chỉ thêm, phải có thêm `CHECK`, `UNIQUE` hoặc trigger trong `schema.sql`. Khi sửa quy tắc, sửa cả code lẫn DB.
- **Test.**
  - Viết trong `tests/test_<phần>.py`, dùng fixture `client`, `conn`, `clock` ở `tests/conftest.py` và helper trong `tests/factories.py`.
  - Mỗi quy tắc có ít nhất một test cho trường hợp sai.
  - Thao tác dễ tranh chấp (tạo đơn, thu tiền) cần thêm một test đồng thời, xem mẫu ở `tests/test_concurrency.py`.
  - Chạy `.venv/bin/python -m pytest` cho toàn bộ test (không chỉ test của mình) trước khi báo xong.

## 6. Điểm nối giữa các phần

**BE1 ↔ BE2**
- **SAL-06 tạo đơn:** gọi `assignments.create_transport_trip(...)`, `assignments.create_rental_record(...)` hoặc `warehouse.create_warehouse_lot(...)`, trong cùng transaction tạo đơn. Gọi lại cùng `order_code` sẽ trả bản ghi cũ.
- **SAL-09 hủy đơn:** gọi `assignments.cancel_operational_record(conn, source_type="TRANSPORT"|"RENTAL"|"WAREHOUSE", source_code=<mã chuyến/hồ sơ thuê/lô>, reason=..., actor=..., now=...)` trong cùng transaction hủy. Hàm này cũng giải phóng lịch xe. Mã bản ghi lấy từ trường `code` mà hàm tạo ở SAL-06 trả về, nên BE1 cần lưu lại mã này cùng đơn.
- **Lúc khởi động app,** gán hàm thật vào `integrations.hooks`:
  - `pending_charge_count(conn, order_code) -> int`: BE2 chặn hoàn tất khi còn phụ phí PENDING.
  - `operation_started(conn, order_code, service_type, at)`: BE1 đổi đơn sang IN_PROGRESS.
  - `operation_completed(conn, order_code, service_type, at)`: BE1 đổi đơn sang COMPLETED.

**BE3 ↔ mọi BE**
- Thay `app.state.principal_provider` bằng xác thực phiên thật (ADM-01/02), trả về `Principal(username, roles)`. Không đổi chữ ký `require_roles`.
- PUB-04 tra cứu công khai đọc mốc vận chuyển từ bảng `trip_milestones`, chỉ trả các trường BR-18 cho phép.

**FE ↔ BE**
- Hợp đồng API lấy từ `/docs` (OpenAPI). Không tự đoán trường.
- **Gọi API:**
  - Gửi `Idempotency-Key` (UUID) cho mọi thao tác xác nhận. Khi bấm lại hoặc gửi lại, giữ nguyên khóa cũ.
  - Gửi `version` khi sửa. Nhận `VERSION_CONFLICT` thì tải lại và báo người dùng.
- **Hiển thị:**
  - Hiện `error.message` cho người dùng. Dùng `details.field` hoặc `details.fields` để tô đỏ đúng ô nhập.
  - Chỉ hiện thao tác hợp lệ theo trạng thái hiện tại của bản ghi.
- **Tệp:** gửi `POST /api/attachments` (multipart, trường `file`), nhận `code`, rồi gửi `attachment_codes` kèm thao tác.
- Website công khai phải dùng tốt ở màn 390px. Cổng nội bộ thiết kế cho 1366×768.

## 7. Không được làm

- Sửa file của phần khác khi chưa hỏi người dùng. Nếu cần đổi điểm nối, ghi rõ đề xuất để người dùng trao đổi với chủ sở hữu.
- Làm các phần ngoài phạm vi SRS: app mobile, GPS, tối ưu tuyến, cổng thanh toán, hóa đơn điện tử, gửi email tự động.
- Commit file bí mật như `.env`, mật khẩu, token.
- Tắt, xóa hoặc nới lỏng test có sẵn để test qua.
- Bật `DEV_AUTH=1` ngoài máy dev.

## 8. Cách làm việc với Git

- Repo: `github.com/minggu151623/mock_project_se` (công khai), nhánh chính là `main`.
- Mỗi phần có một nhánh riêng: `BE1`, `BE2`, `BE3`, `FE1`, `FE2`. Làm việc trên nhánh của phần mình, rồi mở pull request vào `main`.
- Không push thẳng lên `main`, không push lên nhánh của phần khác.
- Trước khi mở PR, toàn bộ test phải qua. Mô tả PR ghi các story đã làm (ví dụ `SAL-04, SAL-05`) và test NEG/E2E đã bao.
