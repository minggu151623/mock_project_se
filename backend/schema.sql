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

-- ================= Kinh doanh và tài chính (BE1) =================
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    customer_type TEXT NOT NULL CHECK(customer_type IN ('INDIVIDUAL','BUSINESS')),
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 200),
    contact_name TEXT NOT NULL CHECK(length(contact_name) BETWEEN 1 AND 200),
    phone TEXT NOT NULL,
    email TEXT,
    address TEXT,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE','INACTIVE')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, created_by TEXT NOT NULL,
    updated_at TEXT NOT NULL, updated_by TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_customers_phone ON customers(phone);
CREATE INDEX IF NOT EXISTS ix_customers_email ON customers(email);

CREATE TABLE IF NOT EXISTS service_requests (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    service_type TEXT NOT NULL CHECK(service_type IN ('TRANSPORT','RENTAL','WAREHOUSE')),
    source TEXT NOT NULL CHECK(source IN ('WEBSITE','INTERNAL')),
    customer_code TEXT REFERENCES customers(code),
    initial_contact_json TEXT NOT NULL,
    service_data_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('NEW','CLARIFYING','QUOTING','CONFIRMED','CLOSED_LOST')),
    assigned_to TEXT,
    public_token_hash TEXT,
    closed_reason TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, created_by TEXT NOT NULL,
    updated_at TEXT NOT NULL, updated_by TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_request_status ON service_requests(status,created_at);

CREATE TABLE IF NOT EXISTS request_events (
    id INTEGER PRIMARY KEY,
    request_code TEXT NOT NULL REFERENCES service_requests(code),
    event_type TEXT NOT NULL, content TEXT NOT NULL,
    actual_at TEXT NOT NULL, recorded_at TEXT NOT NULL, recorded_by TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS trg_request_events_no_update BEFORE UPDATE ON request_events BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END;
CREATE TRIGGER IF NOT EXISTS trg_request_events_no_delete BEFORE DELETE ON request_events BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END;

CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    request_code TEXT NOT NULL REFERENCES service_requests(code),
    version_no INTEGER NOT NULL CHECK(version_no > 0),
    previous_quote_code TEXT REFERENCES quotes(code),
    valid_until TEXT NOT NULL,
    service_data_json TEXT NOT NULL,
    terms TEXT NOT NULL DEFAULT '',
    total_vnd INTEGER NOT NULL DEFAULT 0 CHECK(total_vnd >= 0),
    status TEXT NOT NULL CHECK(status IN ('DRAFT','SENT','ACCEPTED','REJECTED','SUPERSEDED')),
    response_json TEXT,
    row_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, created_by TEXT NOT NULL,
    updated_at TEXT NOT NULL, updated_by TEXT NOT NULL,
    UNIQUE(request_code,version_no)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_quote_pending_request ON quotes(request_code) WHERE status='SENT';
CREATE TRIGGER IF NOT EXISTS trg_quote_state_change
BEFORE UPDATE OF status ON quotes
WHEN NOT (
    (OLD.status='DRAFT' AND NEW.status='SENT') OR
    (OLD.status='SENT' AND NEW.status IN ('ACCEPTED','REJECTED','SUPERSEDED'))
)
BEGIN
    SELECT RAISE(ABORT,'INVALID_QUOTE_TRANSITION');
END;
CREATE TRIGGER IF NOT EXISTS trg_quote_final_update
BEFORE UPDATE ON quotes
WHEN OLD.status<>'DRAFT' AND (
    NEW.request_code IS NOT OLD.request_code OR
    NEW.version_no IS NOT OLD.version_no OR
    NEW.valid_until IS NOT OLD.valid_until OR
    NEW.service_data_json IS NOT OLD.service_data_json OR
    NEW.terms IS NOT OLD.terms OR
    NEW.total_vnd IS NOT OLD.total_vnd
)
BEGIN
    SELECT RAISE(ABORT,'QUOTE_IMMUTABLE');
END;
CREATE TABLE IF NOT EXISTS quote_lines (
    id INTEGER PRIMARY KEY,
    quote_code TEXT NOT NULL REFERENCES quotes(code),
    description TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    unit_price_vnd INTEGER NOT NULL CHECK(unit_price_vnd >= 0),
    line_total_vnd INTEGER NOT NULL CHECK(line_total_vnd >= 0)
);
CREATE TRIGGER IF NOT EXISTS trg_quote_line_insert_draft
BEFORE INSERT ON quote_lines
WHEN (SELECT status FROM quotes WHERE code=NEW.quote_code)<>'DRAFT'
BEGIN
    SELECT RAISE(ABORT,'QUOTE_IMMUTABLE');
END;
CREATE TRIGGER IF NOT EXISTS trg_quote_line_update_draft
BEFORE UPDATE ON quote_lines
WHEN (SELECT status FROM quotes WHERE code=OLD.quote_code)<>'DRAFT'
BEGIN
    SELECT RAISE(ABORT,'QUOTE_IMMUTABLE');
END;
CREATE TRIGGER IF NOT EXISTS trg_quote_line_delete_draft
BEFORE DELETE ON quote_lines
WHEN (SELECT status FROM quotes WHERE code=OLD.quote_code)<>'DRAFT'
BEGIN
    SELECT RAISE(ABORT,'QUOTE_IMMUTABLE');
END;

CREATE TABLE IF NOT EXISTS service_orders (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    request_code TEXT NOT NULL UNIQUE REFERENCES service_requests(code),
    quote_code TEXT NOT NULL UNIQUE REFERENCES quotes(code),
    customer_code TEXT NOT NULL REFERENCES customers(code),
    service_type TEXT NOT NULL CHECK(service_type IN ('TRANSPORT','RENTAL','WAREHOUSE')),
    service_snapshot_json TEXT NOT NULL,
    price_snapshot_vnd INTEGER NOT NULL CHECK(price_snapshot_vnd >= 0),
    operational_code TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('CONFIRMED','IN_PROGRESS','COMPLETED','CANCELLED')),
    payment_due_date TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, created_by TEXT NOT NULL,
    updated_at TEXT NOT NULL, updated_by TEXT NOT NULL,
    cancelled_at TEXT, cancelled_by TEXT, cancel_reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_orders_customer ON service_orders(customer_code,created_at);
CREATE INDEX IF NOT EXISTS ix_orders_due ON service_orders(payment_due_date,status);

CREATE TABLE IF NOT EXISTS charges (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    order_code TEXT NOT NULL REFERENCES service_orders(code),
    amount_vnd INTEGER NOT NULL CHECK(amount_vnd > 0),
    reason TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK(status IN ('PENDING','APPROVED','REJECTED')),
    decision_note TEXT, decided_by TEXT, decided_at TEXT,
    created_at TEXT NOT NULL, created_by TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_charges_order_status ON charges(order_code,status);
CREATE TRIGGER IF NOT EXISTS trg_charge_final_update
BEFORE UPDATE ON charges
WHEN OLD.status<>'PENDING' OR NEW.status NOT IN ('APPROVED','REJECTED')
BEGIN
    SELECT RAISE(ABORT,'CHARGE_IMMUTABLE');
END;
CREATE TRIGGER IF NOT EXISTS trg_charge_no_delete
BEFORE DELETE ON charges
BEGIN
    SELECT RAISE(ABORT,'CHARGE_IMMUTABLE');
END;

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    order_code TEXT NOT NULL REFERENCES service_orders(code),
    amount_vnd INTEGER NOT NULL CHECK(amount_vnd > 0),
    occurred_at TEXT NOT NULL,
    method TEXT NOT NULL CHECK(method IN ('CASH','BANK_TRANSFER')),
    reference TEXT, note TEXT,
    reversal_of TEXT UNIQUE REFERENCES payments(code),
    reason TEXT,
    status TEXT NOT NULL CHECK(status IN ('POSTED','REVERSED')),
    created_at TEXT NOT NULL, created_by TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_payment_reference ON payments(order_code,reference)
 WHERE reference IS NOT NULL AND reversal_of IS NULL;
CREATE INDEX IF NOT EXISTS ix_payments_order ON payments(order_code,created_at);
CREATE TRIGGER IF NOT EXISTS trg_payment_immutable BEFORE UPDATE ON payments
WHEN NOT (OLD.status='POSTED' AND OLD.reversal_of IS NULL AND NEW.status='REVERSED'
    AND NEW.code IS OLD.code AND NEW.order_code IS OLD.order_code
    AND NEW.amount_vnd IS OLD.amount_vnd AND NEW.occurred_at IS OLD.occurred_at
    AND NEW.method IS OLD.method AND NEW.reference IS OLD.reference
    AND NEW.note IS OLD.note AND NEW.reversal_of IS OLD.reversal_of
    AND NEW.reason IS OLD.reason AND NEW.created_at IS OLD.created_at
    AND NEW.created_by IS OLD.created_by)
BEGIN SELECT RAISE(ABORT,'PAYMENT_IMMUTABLE'); END;
CREATE TRIGGER IF NOT EXISTS trg_payment_no_delete BEFORE DELETE ON payments BEGIN SELECT RAISE(ABORT,'PAYMENT_IMMUTABLE'); END;

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

-- ================= Khách, Quản trị và Kiểm thử (BE3 - Vũ) =================

-- Tài khoản, vai trò và phiên đăng nhập (ADM-01, ADM-02).
CREATE TABLE IF NOT EXISTS users (
    id                   INTEGER PRIMARY KEY,
    username             TEXT NOT NULL UNIQUE,
    full_name            TEXT NOT NULL,
    email                TEXT,
    phone                TEXT,
    password_hash        TEXT NOT NULL,
    status               TEXT NOT NULL CHECK (status IN ('ACTIVE', 'LOCKED')),
    must_change_password INTEGER NOT NULL DEFAULT 1 CHECK (must_change_password IN (0, 1)),
    failed_login_count   INTEGER NOT NULL DEFAULT 0 CHECK (failed_login_count >= 0),
    first_failed_at      TEXT,
    locked_until         TEXT,
    lock_reason          TEXT,
    version              INTEGER NOT NULL DEFAULT 1,
    created_at           TEXT NOT NULL,
    created_by           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    updated_by           TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS trg_users_no_delete
BEFORE DELETE ON users
BEGIN
    SELECT RAISE(ABORT, 'USER_NO_DELETE');
END;

CREATE TABLE IF NOT EXISTS user_roles (
    user_id     INTEGER NOT NULL REFERENCES users(id),
    role_code   TEXT NOT NULL CHECK (role_code IN ('SAL', 'DSP', 'WHS', 'FIN', 'MGR', 'ADM')),
    assigned_at TEXT NOT NULL,
    assigned_by TEXT NOT NULL,
    PRIMARY KEY (user_id, role_code)
);
CREATE INDEX IF NOT EXISTS ix_user_roles_role ON user_roles(role_code, user_id);

CREATE TABLE IF NOT EXISTS user_sessions (
    id            INTEGER PRIMARY KEY,
    token_hash    TEXT NOT NULL UNIQUE,
    user_id       INTEGER NOT NULL REFERENCES users(id),
    username      TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    revoked_at    TEXT,
    revoke_reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_user_sessions_user ON user_sessions(user_id, revoked_at);

-- Danh mục dùng chung (ADM-03): chỉ ngừng dùng, không xóa vật lý.
CREATE TABLE IF NOT EXISTS catalog_vehicle_types (
    id                  INTEGER PRIMARY KEY,
    code                TEXT NOT NULL UNIQUE,
    name                TEXT NOT NULL,
    default_capacity_kg INTEGER NOT NULL CHECK (default_capacity_kg > 0),
    description         TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL CHECK (status IN ('ACTIVE', 'INACTIVE')),
    version             INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL,
    created_by          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    updated_by          TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS trg_catalog_vtype_no_delete
BEFORE DELETE ON catalog_vehicle_types
BEGIN
    SELECT RAISE(ABORT, 'CATALOG_NO_DELETE');
END;

CREATE TABLE IF NOT EXISTS catalog_units (
    id          INTEGER PRIMARY KEY,
    code        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL CHECK (status IN ('ACTIVE', 'INACTIVE')),
    version     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    updated_by  TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS trg_catalog_unit_no_delete
BEFORE DELETE ON catalog_units
BEGIN
    SELECT RAISE(ABORT, 'CATALOG_NO_DELETE');
END;

CREATE TABLE IF NOT EXISTS catalog_services (
    id                INTEGER PRIMARY KEY,
    code              TEXT NOT NULL UNIQUE CHECK (code IN ('TRANSPORT', 'RENTAL', 'WAREHOUSE')),
    name              TEXT NOT NULL,
    description       TEXT NOT NULL DEFAULT '',
    warehouse_address TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL CHECK (status IN ('ACTIVE', 'INACTIVE')),
    version           INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL,
    created_by        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    updated_by        TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS trg_catalog_service_no_delete
BEFORE DELETE ON catalog_services
BEGIN
    SELECT RAISE(ABORT, 'CATALOG_NO_DELETE');
END;

-- Website công khai & CMS (PUB-01→05).
CREATE TABLE IF NOT EXISTS public_contents (
    id           INTEGER PRIMARY KEY,
    code         TEXT NOT NULL UNIQUE,
    slug         TEXT NOT NULL UNIQUE,
    content_type TEXT NOT NULL CHECK (content_type IN ('COMPANY', 'SERVICE', 'FAQ', 'CONTACT')),
    service_type TEXT CHECK (service_type IS NULL OR service_type IN ('TRANSPORT', 'RENTAL', 'WAREHOUSE')),
    title        TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT '',
    body_html    TEXT NOT NULL,
    meta_json    TEXT NOT NULL DEFAULT '{}',
    sort_order   INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL CHECK (status IN ('DRAFT', 'PUBLISHED', 'HIDDEN')),
    published_at TEXT,
    published_by TEXT,
    version      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    created_by   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    updated_by   TEXT NOT NULL,
    CHECK ((content_type = 'SERVICE' AND service_type IS NOT NULL)
        OR (content_type <> 'SERVICE' AND service_type IS NULL))
);
CREATE INDEX IF NOT EXISTS ix_public_contents_status ON public_contents(status, content_type, sort_order);

CREATE TRIGGER IF NOT EXISTS trg_public_contents_no_delete
BEFORE DELETE ON public_contents
BEGIN
    SELECT RAISE(ABORT, 'CONTENT_NO_DELETE');
END;

CREATE TABLE IF NOT EXISTS public_content_revisions (
    id           INTEGER PRIMARY KEY,
    content_code TEXT NOT NULL REFERENCES public_contents(code),
    version_no   INTEGER NOT NULL CHECK (version_no > 0),
    title        TEXT NOT NULL,
    summary      TEXT NOT NULL,
    body_html    TEXT NOT NULL,
    status       TEXT NOT NULL,
    changed_at   TEXT NOT NULL,
    changed_by   TEXT NOT NULL,
    reason       TEXT
);
CREATE INDEX IF NOT EXISTS ix_content_revisions_code ON public_content_revisions(content_code, version_no DESC);

CREATE TRIGGER IF NOT EXISTS trg_content_revision_no_update
BEFORE UPDATE ON public_content_revisions
BEGIN
    SELECT RAISE(ABORT, 'REVISION_APPEND_ONLY');
END;

CREATE TRIGGER IF NOT EXISTS trg_content_revision_no_delete
BEFORE DELETE ON public_content_revisions
BEGIN
    SELECT RAISE(ABORT, 'REVISION_APPEND_ONLY');
END;

-- Giới hạn tần suất gửi & tra cứu (PUB-02, PUB-04).
CREATE TABLE IF NOT EXISTS rate_limit_events (
    id          INTEGER PRIMARY KEY,
    bucket      TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rate_limit_bucket_subject_time
    ON rate_limit_events(bucket, subject_key, occurred_at);

-- Index tra cứu lịch sử Audit (ADM-04).
CREATE INDEX IF NOT EXISTS ix_audit_occurred ON audit_log(occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_audit_object ON audit_log(object_type, object_code, id DESC);
CREATE INDEX IF NOT EXISTS ix_audit_actor ON audit_log(actor, occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_audit_action ON audit_log(action, occurred_at DESC);
