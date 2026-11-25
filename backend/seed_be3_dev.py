"""Nạp dữ liệu mẫu cho BE3 (tài khoản 6 vai trò, danh mục dùng chung, nội dung CMS công khai)
và bảo đảm có đủ đơn CONFIRMED cho cả 3 dịch vụ (TRANSPORT, RENTAL, WAREHOUSE) kèm token tra cứu mẫu.

Chạy:
    .venv/bin/python -m backend.seed_be3_dev [đường_dẫn_db]
"""

import sys
from datetime import timedelta

from . import admin, public_site, sales, seed_dev, seed_sales_dev
from .db import connect, init_db, write_tx
from .timeutil import SYSTEM_TZ, system_now

ACTOR = "seed-be3"
DEFAULT_PASSWORD = "Password@123"


def seed(db_path: str = "data/dev.sqlite3") -> dict:
    # Bảo đảm dữ liệu mẫu của BE2 và BE1 đã có trước.
    seed_dev.seed(db_path)
    seed_sales_dev.seed(db_path)

    conn = connect(db_path)
    try:
        init_db(conn)
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            print("BE3 demo records already exist; seed was skipped.")
            return {"skipped": True}

        now = system_now()
        local_today = now.astimezone(SYSTEM_TZ).date()
        lookup_samples: list[dict] = []

        with write_tx(conn):
            # 1. Tài khoản nội bộ cho đủ 6 vai trò + tài khoản khóa + tài khoản đổi mật khẩu lần đầu.
            accounts = [
                ("adm1", "Quản trị hệ thống", ["ADM"], "ACTIVE", False),
                ("sal1", "Nguyễn Kinh Doanh 1", ["SAL"], "ACTIVE", False),
                ("sal2", "Trần Kinh Doanh 2", ["SAL"], "ACTIVE", False),
                ("dsp1", "Lê Điều Phối 1", ["DSP"], "ACTIVE", False),
                ("dsp2", "Phạm Điều Phối 2", ["DSP"], "ACTIVE", False),
                ("whs1", "Hoàng Thủ Kho 1", ["WHS"], "ACTIVE", False),
                ("fin1", "Vũ Kế Toán 1", ["FIN"], "ACTIVE", False),
                ("mgr1", "Đặng Quản Lý 1", ["MGR"], "ACTIVE", False),
                ("firstlogin1", "Nhân viên Mới", ["SAL"], "ACTIVE", True),
                ("locked1", "Tài khoản Đã Khóa", ["SAL"], "LOCKED", False),
            ]
            for username, full_name, roles, status, must_change in accounts:
                admin.create_user(
                    conn,
                    username=username,
                    full_name=full_name,
                    password=DEFAULT_PASSWORD,
                    roles=roles,
                    email=f"{username}@vantai-mock.vn",
                    phone="0901234567",
                    status=status,
                    must_change_password=must_change,
                    actor=ACTOR,
                    now=now,
                )

            # 2. Danh mục dùng chung (ADM-03): Loại xe, Đơn vị hàng hóa, Dịch vụ & Kho.
            for code, name, cap, status in [
                ("TRUCK_2T", "Xe tải thùng kín 2 tấn", 2000, "ACTIVE"),
                ("TRUCK_5T", "Xe tải mui bạt 5 tấn", 5000, "ACTIVE"),
                ("TRUCK_10T", "Xe tải nặng 10 tấn (ngừng dùng)", 10000, "INACTIVE"),
            ]:
                admin.create_vehicle_type(
                    conn,
                    code=code,
                    name=name,
                    default_capacity_kg=cap,
                    status=status,
                    actor=ACTOR,
                    now=now,
                )

            for code, name, status in [
                ("KIEN", "Kiện hàng tiêu chuẩn", "ACTIVE"),
                ("THUNG", "Thùng carton / thùng nhựa", "ACTIVE"),
                ("PALLET", "Pallet tiêu chuẩn", "ACTIVE"),
                ("BAO", "Bao hàng 50kg", "ACTIVE"),
                ("CUON", "Cuộn hàng (ngừng dùng)", "INACTIVE"),
            ]:
                admin.create_unit(
                    conn,
                    code=code,
                    name=name,
                    status=status,
                    actor=ACTOR,
                    now=now,
                )

            for code, name, desc, wh_addr in [
                (
                    "TRANSPORT",
                    "Vận tải hàng hóa đường bộ",
                    "Vận chuyển hàng nguyên chuyến từ kho hoặc điểm lấy đến điểm giao.",
                    "",
                ),
                (
                    "RENTAL",
                    "Cho thuê xe tải theo chuyến/ngày",
                    "Cho thuê xe tải tự lái có biên bản bàn giao và nhận lại minh bạch.",
                    "",
                ),
                (
                    "WAREHOUSE",
                    "Kho trung chuyển hàng hóa",
                    "Lưu kho ngắn hạn, nhập nhiều đợt, xuất từng phần và nối chuyến vận tải.",
                    "Kho Trung chuyển KCN Tân Bình, TP. Hồ Chí Minh",
                ),
            ]:
                admin.upsert_service_catalog(
                    conn,
                    code,
                    name=name,
                    description=desc,
                    warehouse_address=wh_addr,
                    status="ACTIVE",
                    actor=ACTOR,
                    now=now,
                )

            # 3. Nội dung Website công khai (PUB-01, PUB-05): PUBLISHED, DRAFT, HIDDEN.
            cms_pages = [
                (
                    "gioi-thieu-cong-ty",
                    "COMPANY",
                    None,
                    "Giới thiệu Công ty Vận tải & Kho vận Trung chuyển",
                    "Đối tác vận tải, thuê xe tải và kho trung chuyển tin cậy cho doanh nghiệp.",
                    "<p>Chúng tôi cung cấp giải pháp <strong>vận tải hàng hóa</strong>, "
                    "<strong>cho thuê xe tải</strong> và <strong>kho trung chuyển</strong> đồng bộ.</p>",
                    1,
                    "PUBLISHED",
                ),
                (
                    "dich-vu-van-tai",
                    "SERVICE",
                    "TRANSPORT",
                    "Dịch vụ Vận tải hàng hóa",
                    "Nhận hàng tận nơi, theo dõi mốc vận chuyển minh bạch, giao hàng có biên bản.",
                    "<h2>Vận tải hàng hóa</h2><p>Đội xe 2 tấn và 5 tấn sẵn sàng phục vụ.</p>",
                    2,
                    "PUBLISHED",
                ),
                (
                    "dich-vu-thue-xe",
                    "SERVICE",
                    "RENTAL",
                    "Dịch vụ Cho thuê xe tải",
                    "Giữ lịch xe chính xác theo khung giờ, bàn giao và nhận lại rõ ràng.",
                    "<h2>Cho thuê xe tải</h2><p>Đa dạng tải trọng, thủ tục nhanh gọn.</p>",
                    3,
                    "PUBLISHED",
                ),
                (
                    "dich-vu-kho-trung-chuyen",
                    "SERVICE",
                    "WAREHOUSE",
                    "Dịch vụ Kho trung chuyển",
                    "Nhập kho nhiều đợt, xuất kho từng phần và kết nối trực tiếp với chuyến vận tải.",
                    "<h2>Kho trung chuyển</h2><p>Quản lý tồn kho theo từng dòng hàng và đơn vị.</p>",
                    4,
                    "PUBLISHED",
                ),
                (
                    "cau-hoi-thuong-gap",
                    "FAQ",
                    None,
                    "Câu hỏi thường gặp (FAQ)",
                    "Giải đáp về quy trình gửi yêu cầu, báo giá và tra cứu tiến độ.",
                    "<ul><li><strong>Làm sao tra cứu tiến độ?</strong> Dùng mã yêu cầu và token nhận được sau khi gửi form.</li></ul>",
                    5,
                    "PUBLISHED",
                ),
                (
                    "lien-he",
                    "CONTACT",
                    None,
                    "Thông tin liên hệ",
                    "Hotline 1900-6868 – Hỗ trợ từ Thứ 2 đến Thứ 7.",
                    "<p>Địa chỉ: KCN Tân Bình, TP. Hồ Chí Minh. Điện thoại: <strong>1900-6868</strong>.</p>",
                    6,
                    "PUBLISHED",
                ),
                (
                    "bang-gia-du-kien-2027",
                    "COMPANY",
                    None,
                    "Bảng giá dự kiến năm 2027 (Bản nháp)",
                    "Nội dung đang biên tập nội bộ.",
                    "<p>Bản nháp chưa công bố ra website công khai.</p>",
                    7,
                    "DRAFT",
                ),
                (
                    "thong-bao-cu-2025",
                    "COMPANY",
                    None,
                    "Thông báo lịch nghỉ lễ cũ (Đã ẩn)",
                    "Nội dung đã ẩn khỏi website.",
                    "<p>Thông báo cũ đã ngừng hiển thị.</p>",
                    8,
                    "HIDDEN",
                ),
            ]
            for slug, ctype, stype, title, summary, html_body, order_no, status in cms_pages:
                public_site.create_cms_content(
                    conn,
                    slug=slug,
                    content_type=ctype,
                    service_type=stype,
                    title=title,
                    summary=summary,
                    body_html=html_body,
                    sort_order=order_no,
                    status=status,
                    actor="adm1",
                    now=now,
                )

            # 4. Bảo đảm có sẵn đơn CONFIRMED cho cả 3 dịch vụ (TRANSPORT, RENTAL, WAREHOUSE)
            # tạo từ yêu cầu WEBSITE kèm token tra cứu rõ để FE1/FE2 test ngay.
            cust = conn.execute("SELECT * FROM customers WHERE status = 'ACTIVE' LIMIT 1").fetchone()
            if cust is None:
                cust = sales.create_customer(
                    conn,
                    customer_type="BUSINESS",
                    name="Công ty TNHH Thương mại Mẫu",
                    contact_name="Trần Minh Quân",
                    phone="0909111222",
                    email="quan@example.test",
                    address="Quận 3, TP. Hồ Chí Minh",
                    actor="sal1",
                    now=now,
                )
            else:
                cust = dict(cust)

            start_dt = (now + timedelta(days=2)).astimezone(SYSTEM_TZ).replace(hour=8, minute=0, second=0, microsecond=0)
            end_dt = start_dt + timedelta(hours=8)

            website_orders_spec = [
                (
                    "TRANSPORT",
                    {
                        "pickup_address": "Kho Trung chuyển KCN Tân Bình",
                        "delivery_address": "KCN Sóng Thần, Bình Dương",
                        "cargo_description": "Thiết bị điện tử đóng thùng",
                        "cargo_weight_kg": 1500,
                        "planned_pickup_at": start_dt.isoformat(),
                        "committed_end": end_dt.isoformat(),
                    },
                    [{"description": "Cước vận chuyển tuyến Tân Bình - Sóng Thần", "quantity": 1, "unit_price_vnd": 1_800_000}],
                ),
                (
                    "RENTAL",
                    {
                        "vehicle_type": "TRUCK_2T",
                        "min_capacity_kg": 1500,
                        "requested_start": start_dt.isoformat(),
                        "requested_end": end_dt.isoformat(),
                        "purpose": "Chở hàng sự kiện nội thành",
                    },
                    [{"description": "Thuê xe tải 2 tấn theo ngày", "quantity": 1, "unit_price_vnd": 2_200_000}],
                ),
                (
                    "WAREHOUSE",
                    {
                        "cargo_description": "Hàng tiêu dùng đóng kiện và pallet",
                        "expected_in_at": start_dt.isoformat(),
                        "expected_out_at": (start_dt + timedelta(days=5)).isoformat(),
                        "storage_notes": "Bảo quản nơi thoáng mát",
                        "lines": [
                            {"item_code": "SP-THUNG-01", "item_name": "Thùng hàng tiêu dùng", "unit_code": "THUNG", "expected_qty": 120},
                            {"item_code": "SP-PALLET-02", "item_name": "Pallet hàng mẫu", "unit_code": "PALLET", "expected_qty": 10},
                        ],
                    },
                    [{"description": "Phí lưu kho trung chuyển trọn gói", "quantity": 1, "unit_price_vnd": 3_500_000}],
                ),
            ]

            for stype, sdata, qlines in website_orders_spec:
                pub_req = public_site.submit_public_request(
                    conn,
                    client_ip=f"10.0.0.{len(lookup_samples) + 1}",
                    service_type=stype,
                    contact={
                        "name": cust["name"],
                        "contact_name": cust["contact_name"],
                        "phone": cust["phone"],
                        "email": cust["email"],
                    },
                    service_data=sdata,
                    now=now,
                )
                req_row = sales.update_request(
                    conn,
                    pub_req["request_code"],
                    version=1,
                    status="CLARIFYING",
                    customer_code=cust["code"],
                    assigned_to="sal1",
                    clarification="Đã xác nhận thông tin với khách hàng.",
                    actor="sal1",
                    now=now,
                )
                quote = sales.create_quote(
                    conn,
                    request_code=req_row["code"],
                    valid_until=(local_today + timedelta(days=10)).isoformat(),
                    service_data=sdata,
                    terms="Thanh toán trong vòng 14 ngày kể từ ngày xác nhận đơn.",
                    lines=qlines,
                    actor="sal1",
                    now=now,
                )
                sales.issue_quote(conn, quote["code"], actor="sal1", now=now)
                accepted = sales.respond_quote_and_create_order(
                    conn,
                    quote["code"],
                    accepted=True,
                    channel="PHONE",
                    content="Khách đồng ý báo giá và xác nhận lên đơn.",
                    evidence=[],
                    payment_due_date=local_today + timedelta(days=14),
                    actor="sal1",
                    now=now,
                )
                lookup_samples.append({
                    "service_type": stype,
                    "request_code": pub_req["request_code"],
                    "lookup_token": pub_req["lookup_token"],
                    "order_code": accepted["order"]["code"],
                    "operational_code": accepted["order"]["operational_code"],
                })

        print(f"Đã nạp dữ liệu mẫu BE3 vào {db_path} (kèm 3 đơn CONFIRMED cho 3 dịch vụ).")
        return {"skipped": False, "lookup_samples": lookup_samples}
    finally:
        conn.close()


if __name__ == "__main__":
    seed(sys.argv[1] if len(sys.argv) > 1 else "data/dev.sqlite3")
