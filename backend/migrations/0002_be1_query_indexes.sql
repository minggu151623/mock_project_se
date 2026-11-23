-- BE1: support filtered request timelines and payment reconciliation queries.
CREATE INDEX IF NOT EXISTS ix_request_events_request_time
    ON request_events(request_code, actual_at, id);

CREATE INDEX IF NOT EXISTS ix_orders_service_status_created
    ON service_orders(service_type, status, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_payments_order_reference
    ON payments(order_code, reference)
    WHERE reversal_of IS NULL;

ALTER TABLE service_orders
    ADD COLUMN customer_snapshot_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE service_orders
    ADD COLUMN contact_note TEXT NOT NULL DEFAULT '';

CREATE TRIGGER IF NOT EXISTS trg_service_order_snapshot_immutable
BEFORE UPDATE ON service_orders
WHEN NEW.code IS NOT OLD.code
    OR NEW.request_code IS NOT OLD.request_code
    OR NEW.quote_code IS NOT OLD.quote_code
    OR NEW.customer_code IS NOT OLD.customer_code
    OR NEW.service_type IS NOT OLD.service_type
    OR NEW.service_snapshot_json IS NOT OLD.service_snapshot_json
    OR NEW.customer_snapshot_json IS NOT OLD.customer_snapshot_json
    OR NEW.price_snapshot_vnd IS NOT OLD.price_snapshot_vnd
    OR NEW.operational_code IS NOT OLD.operational_code
BEGIN
    SELECT RAISE(ABORT, 'ORDER_SNAPSHOT_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_service_order_status_transition
BEFORE UPDATE OF status ON service_orders
WHEN NOT (
    (OLD.status='CONFIRMED' AND NEW.status IN ('IN_PROGRESS','CANCELLED')) OR
    (OLD.status='IN_PROGRESS' AND NEW.status='COMPLETED')
)
BEGIN
    SELECT RAISE(ABORT, 'INVALID_ORDER_STATUS_TRANSITION');
END;
