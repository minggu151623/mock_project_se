# Backend BE2: Vận hành

Toàn bộ phần BE2 trong bảng phân công: đội xe, lịch nguồn lực, vận tải, thuê xe và kho trung chuyển.

Stack giống các mini project trước: FastAPI + pydantic, SQLite qua `sqlite3`, pytest + httpx.

## Chạy

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest                      # 89 test
.venv/bin/python -m backend.seed_dev            # dữ liệu mẫu vào data/dev.sqlite3
DEV_AUTH=1 .venv/bin/uvicorn backend.main:create_app --factory --reload --port 8765
```

Tài liệu API tự sinh: http://localhost:8765/docs

`DEV_AUTH=1` cho phép gọi API bằng header `X-Dev-User: dsp1` và `X-Dev-Roles: DSP` (hoặc `WHS`, `MGR`, `SAL`). Không bật trên môi trường thật. Không đặt biến này thì mọi request bị từ chối 401, cho tới khi BE3 nối đăng nhập thật.

Chưa có migration. Khi `schema.sql` đổi, xóa `data/` rồi chạy lại `seed_dev`.

## Phạm vi

| Story | Nội dung | Module |
|---|---|---|
| TRN-01 | Xe, tài xế. Biển số so trùng sau chuẩn hóa. Không ngừng xe/tài xế khi còn lịch | `fleet.py` |
| TRN-02 | Phân công, đổi, gỡ phân công chuyến. Đổi thất bại giữ phân công cũ | `assignments.py` |
| TRN-03 | Nhận hàng (chặn vượt tải, chặn khi xe chưa xong việc trước), đang vận chuyển | `trip_ops.py` |
| TRN-04 | Giao hàng cần người nhận và ≥1 tệp bằng chứng; giao không thành công tạo sự cố. Hoàn tất giải phóng xe | `trip_ops.py` |
| TRN-05 | Sự cố OPEN/RESOLVED, cờ quá hạn theo giờ cam kết, đổi giờ kết thúc báo xung đột không ghi đè | `trip_ops.py`, `booking.py` |
| RNT-01, 02 | Giữ, đổi, bỏ giữ xe thuê (bắt buộc lý do và ghi chú trao đổi với khách) | `assignments.py` |
| RNT-03 | Biên bản bàn giao: km, tình trạng, nhiên liệu, phụ kiện, ≥1 ảnh. Đã chốt không sửa, có ghi chú bổ sung | `rental_ops.py` |
| RNT-04 | Nhận lại xe: km trả ≥ km giao, giải phóng xe, hiển thị số phút trả muộn (không tự tính phạt) | `rental_ops.py` |
| RNT-05 | Hoàn tất khi đủ hai biên bản và không còn phụ phí chờ | `rental_ops.py` |
| WHS-01 | Một đơn một lô; dòng hàng có đơn vị cố định; đã có giao dịch thì không đổi mã/đơn vị | `warehouse.py` |
| WHS-02 | Phiếu nhập nháp → xác nhận, nhập nhiều đợt; vượt dự kiến cần lý do chênh lệch | `warehouse.py` |
| WHS-03 | Phiếu xuất từng phần; nháp không trừ tồn; xác nhận chống tồn âm kể cả khi đồng thời | `warehouse.py` |
| WHS-04 | Tra cứu lô, tồn từng dòng, tuổi lô, nhật ký chứng từ; liên kết phiếu xuất với chuyến cùng khách, lấy tại kho | `warehouse.py` |
| WHS-05 | Đề nghị điều chỉnh, MGR duyệt/từ chối; không vượt lượng nguồn, không làm tồn âm | `warehouse.py` |
| WHS-06 | Kết thúc lô khi tồn 0, không còn nháp/điều chỉnh/phụ phí chờ; tồn về 0 không tự đóng | `warehouse.py` |

Test bao các tình huống NEG-06 → NEG-15 trong tài liệu 08, kịch bản E2E-03 trọn vẹn, và phần BE2 của E2E-01, E2E-02.

## Vai trò

| Thao tác | Vai trò |
|---|---|
| Xe, tài xế, phân công, nhận/giao hàng, thuê xe, sự cố | `DSP` ghi; `SAL`, `MGR` xem |
| Kho: dòng hàng, phiếu nhập/xuất, đề nghị điều chỉnh, kết thúc lô | `WHS` ghi; `MGR`, `SAL`, `DSP` xem |
| Duyệt/từ chối điều chỉnh kho, xem hàng đợi duyệt | `MGR` |
| Tải tệp lên | `DSP`, `WHS`; tải về: `DSP`, `WHS`, `SAL`, `MGR` |

`ADM` không mặc nhiên có quyền nghiệp vụ (SRS mục 2).

## Chống sai dữ liệu: ba lớp

Lịch xe và tồn kho đều được bảo vệ theo cùng một cách:

1. **Trong code:** kiểm tra quy tắc, trả mã lỗi rõ ràng và dữ liệu để FE hiển thị (lịch xung đột, tồn hiện tại).
2. **Transaction `BEGIN IMMEDIATE`** (`db.py`): SQLite chỉ cho một người ghi tại một thời điểm, nên "kiểm tra rồi ghi" là nguyên tử. Hai người xác nhận cùng lúc thì người sau thấy dữ liệu mới và nhận lỗi nghiệp vụ, không bị "database is locked".
3. **Trong DB** (`schema.sql`):
   - trigger chặn chồng lịch xe/tài xế;
   - index chặn một xe hoặc tài xế bị dùng cho hai việc cùng lúc;
   - `CHECK on_hand >= 0` chặn tồn âm, `CHECK adjusted_qty <= quantity` chặn điều chỉnh vượt nguồn;
   - trigger chặn sửa/xóa chứng từ kho đã xác nhận, mốc chuyến, biên bản thuê, audit;
   - trigger chặn dòng chứng từ thuộc lô khác, chặn đổi mã/đơn vị dòng hàng đã có giao dịch.

Đã thử cố ý cài lỗi vào code (bỏ từng lớp bảo vệ): lần nào test cũng bắt được.

**Khi sửa quy tắc trùng lịch, sửa cả `booking.find_conflicts` lẫn trigger trong `schema.sql`.**

## Quy tắc chính

- Lịch là khoảng `[bắt đầu, kết thúc)`: 08:00–10:00 và 10:00–12:00 không chồng (BR-07).
- Giữ lịch theo giờ dự kiến; bắt đầu việc theo thực tế. Xe/tài xế còn đang dùng thì chưa nhận hàng hoặc bàn giao việc sau, dù đã quá giờ (BR-09).
- Giờ nhận hàng/bàn giao thực tế phải nằm trong khung đã giữ; ngoài khung thì đổi lịch trước.
- Thời điểm thực tế không ở tương lai, không trước mốc trước đó. Mỗi mốc lưu cả giờ thực tế và giờ nhập.
- Tồn = tổng nhập − tổng xuất − điều chỉnh giảm nhập + điều chỉnh hoàn xuất, chỉ tính chứng từ POSTED. `on_hand` là bản lưu sẵn; `warehouse.ledger_on_hand()` tính lại từ chứng từ để đối chiếu.
- Tồn hiển thị theo từng đơn vị (`on_hand_by_unit`), không bao giờ cộng kiện với pallet (BR-10).
- Xác nhận lại phiếu đã POSTED hoặc duyệt lại điều chỉnh đã duyệt: trả kết quả cũ, không đổi tồn lần nữa.
- Thời gian lưu UTC dạng `2026-12-01T01:00:00Z`. API nhận ISO 8601; giờ không kèm múi giờ hiểu là Asia/Ho_Chi_Minh.

## Mã lỗi

Mọi lỗi có dạng `{"error": {"code", "message", "details"}}`. Thông báo bằng tiếng Việt.

| Mã | HTTP | Khi nào |
|---|---|---|
| `BOOKING_CONFLICT` | 409 | Chồng lịch. `details.conflicts` liệt kê lịch xung đột |
| `VEHICLE_NOT_ELIGIBLE`, `DRIVER_NOT_ELIGIBLE` | 409 | Xe không ACTIVE, tài xế INACTIVE |
| `CAPACITY_INSUFFICIENT`, `VEHICLE_TYPE_MISMATCH`, `OVER_CAPACITY` | 409 | Không đủ tải, sai loại xe thuê, hàng thực tế vượt tải lúc nhận |
| `RESOURCE_STILL_IN_USE` | 409 | Xe/tài xế chưa trả thực tế (BR-09) |
| `OUTSIDE_BOOKED_WINDOW` | 409 | Bắt đầu thực tế ngoài khung đã giữ |
| `FUTURE_BOOKINGS_EXIST`, `RESOURCE_IN_USE` | 409 | Đổi trạng thái xe/tài xế khi còn lịch |
| `INSUFFICIENT_STOCK` | 409 | Không đủ tồn. `details.lines` có tồn hiện tại |
| `ADJUSTMENT_EXCEEDS_SOURCE` | 409 | Điều chỉnh vượt phần còn điều chỉnh được |
| `TRANSPORT_LINK_INVALID` | 409 | Chuyến khác khách, đã hủy/hoàn tất, hoặc không lấy hàng tại kho |
| `LOT_NOT_CLOSABLE` | 409 | Còn tồn, phiếu nháp hoặc phụ phí chờ. `details` liệt kê từng việc |
| `LOT_LINE_IN_USE`, `LOT_CLOSED` | 409 | Dòng hàng đã có chứng từ; lô đã đóng |
| `PENDING_CHARGES`, `EVIDENCE_REQUIRED` | 409 | Còn phụ phí chờ duyệt; thiếu bằng chứng/biên bản |
| `INVALID_STATE` | 409 | Sai trạng thái (bỏ bước, làm lại thao tác đã xong) |
| `VERSION_CONFLICT` | 409 | Người khác đã sửa hồ sơ; gửi kèm `version` mới nhất |
| `IDEMPOTENCY_KEY_REUSED`, `ATTACHMENT_ALREADY_USED` | 409 | Dùng lại khóa cho nội dung khác; tệp đã làm bằng chứng nơi khác |
| `NOT_CANCELLABLE` | 409 | Hủy đơn khi đã bắt đầu thực hiện |
| `VALIDATION_ERROR` | 422 | Dữ liệu sai. `details.field` hoặc `details.fields` chỉ trường cần sửa |

## Điểm nối với các phần khác

**BE1 (đơn hàng, phụ phí):**
- SAL-06 gọi `assignments.create_transport_trip()`, `assignments.create_rental_record()`, `warehouse.create_warehouse_lot()` khi sinh đơn. Gọi lại cùng đơn trả bản ghi cũ.
- SAL-09 gọi `assignments.cancel_operational_record(source_type=TRANSPORT|RENTAL|WAREHOUSE, ...)` trong transaction hủy đơn.
- Gán hàm thật vào `integrations.hooks` (xem docstring `integrations.py`):
  - `pending_charge_count(conn, order_code)`: số phụ phí PENDING; BE2 chặn hoàn tất khi > 0.
  - `operation_started(...)` / `operation_completed(...)`: BE2 gọi khi nhận hàng, bàn giao xe, nhập kho đầu tiên, và khi hoàn tất, để BE1 đổi đơn sang IN_PROGRESS / COMPLETED trong cùng transaction.
- `idempotency.py` là bản tối thiểu; BE1 có thể thay bằng helper chung.

**BE3 (đăng nhập, audit, tệp, kiểm thử):**
- Thay `app.state.principal_provider` bằng xác thực phiên thật.
- `audit.py` và `attachments.py` là bản tối thiểu theo BR-17 và NFR-07; BE3 có thể thay bằng dịch vụ chung, giữ nguyên tên hàm.
- PUB-04 (tra cứu công khai) đọc mốc từ bảng `trip_milestones`; chỉ trả trường được phép theo BR-18.

**FE2:**
- Xem request/response ở `/docs`.
- Gửi `Idempotency-Key` (ví dụ UUID) cho các thao tác xác nhận: phân công, giữ xe, nhận/giao hàng, bàn giao/trả xe, lập phiếu kho, đề nghị điều chỉnh. Bấm lại thì giữ nguyên khóa.
- Luôn gửi `version` khi sửa; nhận `VERSION_CONFLICT` thì tải lại.
- Tệp: `POST /api/attachments` (multipart, trường `file`) trả `code`, rồi gửi `attachment_codes` trong thao tác giao hàng/bàn giao/trả xe.
- Phiếu xuất nháp có `stock_note` và `current_on_hand` để hiển thị "tồn sẽ được kiểm tra lại khi xác nhận".

## Chưa làm / ngoài phần BE2

- Đăng nhập thật, danh mục loại xe và đơn vị (ADM-03), migration có version: thuộc BE3.
- Đơn hàng, phụ phí, thanh toán: thuộc BE1 (đã có điểm nối).
- Giao diện: thuộc FE2.
- Tệp tải lên nhưng không gắn vào hồ sơ nào hiện chưa được dọn tự động.
