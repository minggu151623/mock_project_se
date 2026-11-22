-- Schema cho phần đội xe và engine giữ lịch (BE2).
-- Thời gian lưu dạng chuỗi UTC cố định 'YYYY-MM-DDTHH:MM:SSZ' để so sánh chuỗi
-- cũng chính là so sánh thời gian. CHECK GLOB bên dưới chặn mọi định dạng khác.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS code_sequences (
    prefix     TEXT PRIMARY KEY,
    last_value INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS vehicles (
    id           INTEGER PRIMARY KEY,
    plate_number TEXT NOT NULL,
    plate_key    TEXT NOT NULL UNIQUE,
    vehicle_type TEXT NOT NULL,
    capacity_kg  INTEGER NOT NULL CHECK (capacity_kg > 0),
    description  TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL CHECK (status IN ('ACTIVE', 'MAINTENANCE', 'INACTIVE')),
    version      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    created_by   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    updated_by   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drivers (
    id         INTEGER PRIMARY KEY,
    code       TEXT NOT NULL UNIQUE,
    full_name  TEXT NOT NULL,
    phone      TEXT NOT NULL,
    status     TEXT NOT NULL CHECK (status IN ('ACTIVE', 'INACTIVE')),
    version    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL
);

-- Lịch giữ dùng chung cho vận tải và thuê xe (BR-07).
-- HELD: đã giữ, chưa dùng. IN_USE: đang dùng thực tế. RELEASED: đã giải phóng.
CREATE TABLE IF NOT EXISTS resource_bookings (
    id             INTEGER PRIMARY KEY,
    code           TEXT NOT NULL UNIQUE,
    source_type    TEXT NOT NULL CHECK (source_type IN ('TRANSPORT', 'RENTAL')),
    source_code    TEXT NOT NULL,
    vehicle_id     INTEGER NOT NULL REFERENCES vehicles(id),
    driver_id      INTEGER REFERENCES drivers(id),
    planned_start  TEXT NOT NULL CHECK (planned_start GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    planned_end    TEXT NOT NULL CHECK (planned_end   GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    status         TEXT NOT NULL CHECK (status IN ('HELD', 'IN_USE', 'RELEASED')),
    actual_start   TEXT,
    actual_end     TEXT,
    release_reason TEXT,
    version        INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL,
    created_by     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    updated_by     TEXT NOT NULL,
    CHECK (planned_end > planned_start),
    -- Vận tải luôn có tài xế; thuê xe không kèm tài xế.
    CHECK ((source_type = 'TRANSPORT' AND driver_id IS NOT NULL)
        OR (source_type = 'RENTAL' AND driver_id IS NULL)),
    CHECK (status <> 'IN_USE' OR actual_start IS NOT NULL)
);

-- Mỗi chuyến/hồ sơ thuê chỉ có tối đa một lịch giữ còn hiệu lực.
CREATE UNIQUE INDEX IF NOT EXISTS ux_booking_active_source
    ON resource_bookings(source_type, source_code) WHERE status <> 'RELEASED';
-- BR-09: một xe/tài xế chỉ được đang dùng cho một việc tại một thời điểm.
CREATE UNIQUE INDEX IF NOT EXISTS ux_booking_vehicle_in_use
    ON resource_bookings(vehicle_id) WHERE status = 'IN_USE';
CREATE UNIQUE INDEX IF NOT EXISTS ux_booking_driver_in_use
    ON resource_bookings(driver_id) WHERE status = 'IN_USE' AND driver_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_booking_vehicle_window
    ON resource_bookings(vehicle_id, planned_start, planned_end) WHERE status <> 'RELEASED';
CREATE INDEX IF NOT EXISTS ix_booking_driver_window
    ON resource_bookings(driver_id, planned_start, planned_end) WHERE status <> 'RELEASED';

-- Lưới an toàn BR-07: từ chối mọi lịch chồng nhau dù ghi bằng đường nào.
-- Khoảng [start, end): A chồng B khi A.start < B.end AND B.start < A.end.
CREATE TRIGGER IF NOT EXISTS trg_booking_vehicle_overlap_insert
BEFORE INSERT ON resource_bookings
WHEN NEW.status <> 'RELEASED' AND EXISTS (
    SELECT 1 FROM resource_bookings b
    WHERE b.vehicle_id = NEW.vehicle_id AND b.status <> 'RELEASED'
      AND b.planned_start < NEW.planned_end AND NEW.planned_start < b.planned_end)
BEGIN
    SELECT RAISE(ABORT, 'BOOKING_OVERLAP_VEHICLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_booking_vehicle_overlap_update
BEFORE UPDATE OF vehicle_id, planned_start, planned_end, status ON resource_bookings
WHEN NEW.status <> 'RELEASED' AND EXISTS (
    SELECT 1 FROM resource_bookings b
    WHERE b.id <> NEW.id AND b.vehicle_id = NEW.vehicle_id AND b.status <> 'RELEASED'
      AND b.planned_start < NEW.planned_end AND NEW.planned_start < b.planned_end)
BEGIN
    SELECT RAISE(ABORT, 'BOOKING_OVERLAP_VEHICLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_booking_driver_overlap_insert
BEFORE INSERT ON resource_bookings
WHEN NEW.status <> 'RELEASED' AND NEW.driver_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM resource_bookings b
    WHERE b.driver_id = NEW.driver_id AND b.status <> 'RELEASED'
      AND b.planned_start < NEW.planned_end AND NEW.planned_start < b.planned_end)
BEGIN
    SELECT RAISE(ABORT, 'BOOKING_OVERLAP_DRIVER');
END;

CREATE TRIGGER IF NOT EXISTS trg_booking_driver_overlap_update
BEFORE UPDATE OF driver_id, planned_start, planned_end, status ON resource_bookings
WHEN NEW.status <> 'RELEASED' AND NEW.driver_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM resource_bookings b
    WHERE b.id <> NEW.id AND b.driver_id = NEW.driver_id AND b.status <> 'RELEASED'
      AND b.planned_start < NEW.planned_end AND NEW.planned_start < b.planned_end)
BEGIN
    SELECT RAISE(ABORT, 'BOOKING_OVERLAP_DRIVER');
END;

-- Lịch đã giải phóng không được mở lại.
CREATE TRIGGER IF NOT EXISTS trg_booking_no_reopen
BEFORE UPDATE OF status ON resource_bookings
WHEN OLD.status = 'RELEASED' AND NEW.status <> 'RELEASED'
BEGIN
    SELECT RAISE(ABORT, 'BOOKING_RELEASED_IMMUTABLE');
END;

-- Bản ghi vận hành tối thiểu. SAL-06 (BE1) sẽ tạo các dòng này khi sinh đơn.
CREATE TABLE IF NOT EXISTS transport_trips (
    id              INTEGER PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,
    order_code      TEXT NOT NULL UNIQUE,
    customer_code   TEXT NOT NULL DEFAULT '',
    cargo_description TEXT NOT NULL DEFAULT '',
    cargo_weight_kg REAL NOT NULL CHECK (cargo_weight_kg > 0),
    pickup_address  TEXT NOT NULL DEFAULT '',
    delivery_address TEXT NOT NULL DEFAULT '',
    -- Điểm lấy là kho trung chuyển: điều kiện để liên kết phiếu xuất kho (WHS-04).
    pickup_from_warehouse INTEGER NOT NULL DEFAULT 0 CHECK (pickup_from_warehouse IN (0, 1)),
    -- Thời điểm kết thúc cam kết với khách, dùng cho cờ quá hạn vận hành (TRN-05).
    committed_end   TEXT,
    status          TEXT NOT NULL CHECK (status IN ('UNASSIGNED', 'ASSIGNED', 'PICKED_UP',
                        'IN_TRANSIT', 'DELIVERED', 'COMPLETED', 'CANCELLED')),
    booking_id      INTEGER REFERENCES resource_bookings(id),
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    updated_by      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rental_records (
    id              INTEGER PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,
    order_code      TEXT NOT NULL UNIQUE,
    vehicle_type    TEXT NOT NULL,
    min_capacity_kg INTEGER NOT NULL CHECK (min_capacity_kg > 0),
    requested_start TEXT NOT NULL,
    requested_end   TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('UNASSIGNED', 'RESERVED', 'HANDED_OVER',
                        'RETURNED', 'COMPLETED', 'CANCELLED')),
    booking_id      INTEGER REFERENCES resource_bookings(id),
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    updated_by      TEXT NOT NULL,
    CHECK (requested_end > requested_start)
);

-- Audit tối thiểu theo BR-17. BE3 sẽ mở rộng; bảng chỉ cho thêm, không sửa/xóa.
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_code TEXT NOT NULL,
    before_json TEXT,
    after_json  TEXT,
    reason      TEXT
);

CREATE TRIGGER IF NOT EXISTS trg_audit_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'AUDIT_APPEND_ONLY');
END;

CREATE TRIGGER IF NOT EXISTS trg_audit_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'AUDIT_APPEND_ONLY');
END;

-- Chống gửi trùng (NFR-05). BE1 sở hữu helper này và có thể mở rộng.
CREATE TABLE IF NOT EXISTS idempotency_keys (
    actor         TEXT NOT NULL,
    key           TEXT NOT NULL,
    operation     TEXT NOT NULL,
    request_hash  TEXT NOT NULL,
    status_code   INTEGER NOT NULL,
    response_json TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (actor, key)
);

-- ================= Thực hiện chuyến (TRN-03/04/05) =================

-- Mỗi mốc chỉ ghi một lần; gửi lại không nhân đôi mốc (TRN-04).
CREATE TABLE IF NOT EXISTS trip_milestones (
    id           INTEGER PRIMARY KEY,
    trip_code    TEXT NOT NULL REFERENCES transport_trips(code),
    milestone    TEXT NOT NULL CHECK (milestone IN ('PICKED_UP', 'IN_TRANSIT', 'DELIVERED', 'COMPLETED')),
    actual_at    TEXT NOT NULL,
    recorded_at  TEXT NOT NULL,
    recorded_by  TEXT NOT NULL,
    person_name  TEXT,
    note         TEXT,
    details_json TEXT,
    UNIQUE (trip_code, milestone)
);

CREATE TRIGGER IF NOT EXISTS trg_milestone_no_update
BEFORE UPDATE ON trip_milestones
BEGIN
    SELECT RAISE(ABORT, 'MILESTONE_APPEND_ONLY');
END;

CREATE TRIGGER IF NOT EXISTS trg_milestone_no_delete
BEFORE DELETE ON trip_milestones
BEGIN
    SELECT RAISE(ABORT, 'MILESTONE_APPEND_ONLY');
END;

CREATE TABLE IF NOT EXISTS incidents (
    id                     INTEGER PRIMARY KEY,
    code                   TEXT NOT NULL UNIQUE,
    trip_code              TEXT NOT NULL REFERENCES transport_trips(code),
    incident_type          TEXT NOT NULL CHECK (incident_type IN
                               ('DELAY', 'BREAKDOWN', 'DAMAGE', 'DELIVERY_FAILED', 'OTHER')),
    occurred_at            TEXT NOT NULL,
    description            TEXT NOT NULL,
    handler_name           TEXT NOT NULL,
    expected_resolution_at TEXT,
    status                 TEXT NOT NULL CHECK (status IN ('OPEN', 'RESOLVED')),
    resolution             TEXT,
    resolved_at            TEXT,
    resolved_by            TEXT,
    version                INTEGER NOT NULL DEFAULT 1,
    created_at             TEXT NOT NULL,
    created_by             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    updated_by             TEXT NOT NULL,
    CHECK (status <> 'RESOLVED' OR (resolution IS NOT NULL AND resolved_at IS NOT NULL))
);

-- Tệp đính kèm tối thiểu (NFR-07). BE3 sở hữu dịch vụ tệp và có thể thay phần này.
CREATE TABLE IF NOT EXISTS attachments (
    id            INTEGER PRIMARY KEY,
    code          TEXT NOT NULL UNIQUE,
    original_name TEXT NOT NULL,
    stored_name   TEXT NOT NULL UNIQUE,
    content_type  TEXT NOT NULL CHECK (content_type IN ('application/pdf', 'image/jpeg', 'image/png')),
    size_bytes    INTEGER NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 5242880),
    sha256        TEXT NOT NULL,
    owner_type    TEXT,
    owner_code    TEXT,
    uploaded_at   TEXT NOT NULL,
    uploaded_by   TEXT NOT NULL,
    linked_at     TEXT,
    linked_by     TEXT
);
CREATE INDEX IF NOT EXISTS ix_attachment_owner ON attachments(owner_type, owner_code);

-- Tệp đã gắn làm bằng chứng thì không gắn sang hồ sơ khác.
CREATE TRIGGER IF NOT EXISTS trg_attachment_link_once
BEFORE UPDATE OF owner_type, owner_code ON attachments
WHEN OLD.owner_code IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'ATTACHMENT_ALREADY_LINKED');
END;

-- ================= Thực hiện thuê xe (RNT-03/04/05) =================

-- Biên bản bàn giao và nhận lại; đã xác nhận thì không sửa/xóa.
CREATE TABLE IF NOT EXISTS rental_inspections (
    id             INTEGER PRIMARY KEY,
    rental_code    TEXT NOT NULL REFERENCES rental_records(code),
    kind           TEXT NOT NULL CHECK (kind IN ('HANDOVER', 'RETURN')),
    actual_at      TEXT NOT NULL,
    person_name    TEXT NOT NULL,
    odometer_km    INTEGER NOT NULL CHECK (odometer_km >= 0),
    body_condition TEXT NOT NULL,
    fuel_level     TEXT NOT NULL,
    accessories    TEXT NOT NULL,
    note           TEXT,
    recorded_at    TEXT NOT NULL,
    recorded_by    TEXT NOT NULL,
    UNIQUE (rental_code, kind)
);

CREATE TRIGGER IF NOT EXISTS trg_inspection_no_update
BEFORE UPDATE ON rental_inspections
BEGIN
    SELECT RAISE(ABORT, 'INSPECTION_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_inspection_no_delete
BEFORE DELETE ON rental_inspections
BEGIN
    SELECT RAISE(ABORT, 'INSPECTION_IMMUTABLE');
END;

-- Ghi chú bổ sung có tác giả và thời gian (RNT-03).
CREATE TABLE IF NOT EXISTS record_notes (
    id         INTEGER PRIMARY KEY,
    owner_type TEXT NOT NULL,
    owner_code TEXT NOT NULL,
    note       TEXT NOT NULL,
    author     TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS trg_note_no_update
BEFORE UPDATE ON record_notes
BEGIN
    SELECT RAISE(ABORT, 'NOTE_APPEND_ONLY');
END;

CREATE TRIGGER IF NOT EXISTS trg_note_no_delete
BEFORE DELETE ON record_notes
BEGIN
    SELECT RAISE(ABORT, 'NOTE_APPEND_ONLY');
END;

-- ================= Kho trung chuyển (WHS-01→06) =================

-- Một đơn kho có một lô (WHS-01). SAL-06 (BE1) tạo bản ghi này khi sinh đơn.
CREATE TABLE IF NOT EXISTS warehouse_lots (
    id                INTEGER PRIMARY KEY,
    code              TEXT NOT NULL UNIQUE,
    order_code        TEXT NOT NULL UNIQUE,
    customer_code     TEXT NOT NULL,
    expected_in_at    TEXT,
    expected_out_at   TEXT,
    storage_notes     TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL CHECK (status IN ('WAITING_RECEIPT', 'IN_STORAGE', 'COMPLETED', 'CANCELLED')),
    first_received_at TEXT,
    completed_at      TEXT,
    completed_by      TEXT,
    version           INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL,
    created_by        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    updated_by        TEXT NOT NULL
);

-- on_hand là bản lưu sẵn của: tổng nhập − tổng xuất ± điều chỉnh đã POSTED (BR-11).
-- CHECK on_hand >= 0 là lưới an toàn chống tồn âm.
CREATE TABLE IF NOT EXISTS lot_lines (
    id           INTEGER PRIMARY KEY,
    lot_id       INTEGER NOT NULL REFERENCES warehouse_lots(id),
    item_code    TEXT NOT NULL,
    item_name    TEXT NOT NULL,
    unit_code    TEXT NOT NULL,
    expected_qty INTEGER NOT NULL CHECK (expected_qty > 0),
    on_hand      INTEGER NOT NULL DEFAULT 0 CHECK (on_hand >= 0),
    version      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    created_by   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    updated_by   TEXT NOT NULL,
    UNIQUE (lot_id, item_code)
);

CREATE TABLE IF NOT EXISTS warehouse_documents (
    id                  INTEGER PRIMARY KEY,
    code                TEXT NOT NULL UNIQUE,
    lot_id              INTEGER NOT NULL REFERENCES warehouse_lots(id),
    doc_type            TEXT NOT NULL CHECK (doc_type IN ('RECEIPT', 'ISSUE', 'ADJUSTMENT')),
    status              TEXT NOT NULL CHECK (status IN ('DRAFT', 'POSTED', 'CANCELLED')),
    occurred_at         TEXT,
    counterpart_name    TEXT,
    condition_note      TEXT,
    discrepancy_reason  TEXT,
    destination         TEXT,
    transport_trip_code TEXT REFERENCES transport_trips(code),
    source_document_id  INTEGER REFERENCES warehouse_documents(id),
    reason              TEXT,
    decision_note       TEXT,
    decided_by          TEXT,
    decided_at          TEXT,
    posted_at           TEXT,
    posted_by           TEXT,
    version             INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL,
    created_by          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    updated_by          TEXT NOT NULL,
    CHECK (doc_type = 'ISSUE' OR transport_trip_code IS NULL),
    CHECK ((doc_type = 'ADJUSTMENT') = (source_document_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ix_doc_lot ON warehouse_documents(lot_id, status);

-- adjusted_qty: tổng điều chỉnh đã POSTED trỏ vào dòng nguồn này; không vượt quantity (BR-12).
CREATE TABLE IF NOT EXISTS document_lines (
    id             INTEGER PRIMARY KEY,
    document_id    INTEGER NOT NULL REFERENCES warehouse_documents(id),
    lot_line_id    INTEGER NOT NULL REFERENCES lot_lines(id),
    quantity       INTEGER NOT NULL CHECK (quantity > 0),
    source_line_id INTEGER REFERENCES document_lines(id),
    adjusted_qty   INTEGER NOT NULL DEFAULT 0 CHECK (adjusted_qty >= 0 AND adjusted_qty <= quantity),
    note           TEXT,
    UNIQUE (document_id, lot_line_id)
);
CREATE INDEX IF NOT EXISTS ix_doc_line_lot_line ON document_lines(lot_line_id);

-- Chứng từ đã POSTED/CANCELLED là bản chốt: không mở lại, không đổi nội dung (BR-11).
CREATE TRIGGER IF NOT EXISTS trg_doc_final_status
BEFORE UPDATE OF status ON warehouse_documents
WHEN OLD.status IN ('POSTED', 'CANCELLED') AND NEW.status <> OLD.status
BEGIN
    SELECT RAISE(ABORT, 'WAREHOUSE_DOCUMENT_FINAL');
END;

CREATE TRIGGER IF NOT EXISTS trg_doc_final_fields
BEFORE UPDATE OF lot_id, doc_type, occurred_at, counterpart_name, transport_trip_code, source_document_id
ON warehouse_documents
WHEN OLD.status <> 'DRAFT' AND (
    NEW.lot_id IS NOT OLD.lot_id OR NEW.doc_type IS NOT OLD.doc_type OR NEW.occurred_at IS NOT OLD.occurred_at
    OR NEW.counterpart_name IS NOT OLD.counterpart_name OR NEW.transport_trip_code IS NOT OLD.transport_trip_code
    OR NEW.source_document_id IS NOT OLD.source_document_id)
BEGIN
    SELECT RAISE(ABORT, 'WAREHOUSE_DOCUMENT_FINAL');
END;

CREATE TRIGGER IF NOT EXISTS trg_doc_line_insert_final
BEFORE INSERT ON document_lines
WHEN (SELECT status FROM warehouse_documents WHERE id = NEW.document_id) <> 'DRAFT'
BEGIN
    SELECT RAISE(ABORT, 'WAREHOUSE_DOCUMENT_FINAL');
END;

CREATE TRIGGER IF NOT EXISTS trg_doc_line_update_final
BEFORE UPDATE OF document_id, lot_line_id, quantity, source_line_id ON document_lines
WHEN (NEW.document_id IS NOT OLD.document_id OR NEW.lot_line_id IS NOT OLD.lot_line_id
      OR NEW.quantity IS NOT OLD.quantity OR NEW.source_line_id IS NOT OLD.source_line_id)
 AND (SELECT status FROM warehouse_documents WHERE id = OLD.document_id) <> 'DRAFT'
BEGIN
    SELECT RAISE(ABORT, 'WAREHOUSE_DOCUMENT_FINAL');
END;

CREATE TRIGGER IF NOT EXISTS trg_doc_line_delete_final
BEFORE DELETE ON document_lines
WHEN (SELECT status FROM warehouse_documents WHERE id = OLD.document_id) <> 'DRAFT'
BEGIN
    SELECT RAISE(ABORT, 'WAREHOUSE_DOCUMENT_FINAL');
END;

-- Dòng chứng từ phải thuộc đúng lô của chứng từ: không xuất hàng của lô khác (WHS-03).
CREATE TRIGGER IF NOT EXISTS trg_doc_line_same_lot
BEFORE INSERT ON document_lines
WHEN (SELECT lot_id FROM lot_lines WHERE id = NEW.lot_line_id)
  <> (SELECT lot_id FROM warehouse_documents WHERE id = NEW.document_id)
BEGIN
    SELECT RAISE(ABORT, 'LOT_LINE_MISMATCH');
END;

-- Dòng hàng đã có giao dịch thì không đổi mã/đơn vị (WHS-01, BR-10).
CREATE TRIGGER IF NOT EXISTS trg_lot_line_identity
BEFORE UPDATE OF item_code, unit_code ON lot_lines
WHEN (NEW.item_code IS NOT OLD.item_code OR NEW.unit_code IS NOT OLD.unit_code)
 AND EXISTS (SELECT 1 FROM document_lines dl JOIN warehouse_documents d ON d.id = dl.document_id
             WHERE dl.lot_line_id = OLD.id AND d.status = 'POSTED')
BEGIN
    SELECT RAISE(ABORT, 'LOT_LINE_IN_USE');
END;
