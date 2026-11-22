# Phân công hệ thống quản lý vận tải, thuê xe và kho trung chuyển

Mock project · Software Engineer · 5 thành viên · **2 FE + 3 BE (BE3 kiêm kiểm thử)**

Tổng hợp từ 9 tài liệu `00_Overall_SRS` đến `08_Acceptance_and_Traceability`.

**Quy mô:** 40 user story (39 Must, 1 Should) · 7 phân hệ · 12 NFR · 18 business rule · 3 kịch bản E2E · 24 test ngoại lệ (NEG)

> Thay FE1, FE2, BE1, BE2, BE3 bằng tên thật của nhóm. Điểm là ước lượng tương đối (thang 2/3/5/8), giả định mỗi story khoảng 35% công FE và 65% công BE. Nhóm nên ước lượng lại bằng planning poker.

---

## 1. Một project phần mềm gồm những phần nào

- **FE**: giao diện. Gồm website công khai cho khách (không đăng nhập, chạy tốt ở 390px) và cổng nội bộ cho 6 vai trò nhân viên (1366×768).
- **BE**: API và quy tắc nghiệp vụ. SRS yêu cầu mọi quy tắc và quyền phải được kiểm tra ở backend, kể cả khi gọi API trực tiếp, nên đây là phần nặng nhất.
- **Data**: ở project này là cơ sở dữ liệu (bảng, ràng buộc, migration, dữ liệu mẫu). Không có Data Science hay ML.
- **DevOps**: deploy, cấu hình môi trường, HTTPS, sao lưu (NFR-09, NFR-11).
- **Kiểm thử**: 3 E2E, 24 NEG, kiểm thử NFR, báo cáo UAT (tài liệu 08). Bắt buộc có bằng chứng cho mọi story Must.

Ngoài phạm vi (SRS 1.2): app mobile, GPS, tối ưu tuyến, cổng thanh toán, hóa đơn điện tử, gửi email tự động.

---

## 2. Bảng phân công

| Người | Phần | Phạm vi | Việc thêm | Điểm |
|---|---|---|---|---|
| **FE1** | Giao diện khách, quản trị, kinh doanh | Màn hình của PUB-01→05, ADM-01→04, SAL-01→09 | Bộ component chung (5), CI/CD và deploy (5), sao lưu (3), quản lý API giả (4) | ~45 |
| **FE2** | Giao diện vận hành | Màn hình của TRN-01→05, RNT-01→05, WHS-01→06, PAY-01→04, ADM-05/06 | Truy vấn dashboard và xuất CSV phía BE (7), dữ liệu UAT (3) | ~48 |
| **BE1** | Kinh doanh + Tài chính | BE của SAL-01→09, PAY-01→04 | Thiết kế DB và quy ước migration (3), chống gửi trùng + optimistic locking (5) | ~47 |
| **BE2** | Vận hành | BE của TRN-01→05, RNT-01→05, WHS-01→06 | Engine giữ lịch xe (đã tính trong TRN-02) | ~53 |
| **BE3** | Khách + Quản trị + **Kiểm thử** | BE của PUB-01→05, ADM-01→04 | Kiểm thử (14), đo hiệu năng (5), upload tệp (3) | ~47 |

Tổng: 240 điểm (190 điểm story + 50 điểm việc thêm).

### Việc kiểm thử của BE3
- Kế hoạch kiểm thử, ma trận truy vết story → test case, quản lý bug và báo cáo UAT (3)
- Viết test case và chạy 24 NEG + 3 E2E, ghi kết quả theo mẫu tài liệu 08 mục 1 (8)
- Kiểm thử bảo mật và NFR: gọi API sai quyền, token tra cứu, giới hạn gửi, tệp sai loại, script trong input (3)
- Đo hiệu năng NFR-04: 100.000 đơn, 1.000.000 giao dịch kho, p95 ≤ 2s đọc / ≤ 3s ghi (5)

Ba người dev còn lại (BE1, BE2 và cả hai FE) vẫn tự viết unit test cho code của mình và sửa bug BE3 báo.

### Vì sao BE3 kiêm kiểm thử
- Sau khi chuyển Kho sang BE2, phần BE của BE3 nhẹ nhất.
- Phần BE3 viết (đăng nhập, phân quyền, audit) chính là nền cho kiểm thử bảo mật.
- BE3 kiểm thử phần nghiệp vụ do người khác viết, nên kết quả khách quan hơn.

### Cặp làm việc
- **FE1** ghép với **BE1** (kinh doanh) và **BE3** (website, quản trị)
- **FE2** ghép với **BE2** (vận hành) và **BE1** (thanh toán)

### Phần khó nhất của từng người
- **FE1:** Bộ component chung phải xong sớm vì FE2 dùng lại. Editor báo giá có nhiều phiên bản và trạng thái. Website phải chạy ở 390px.
- **FE2:** Màn vận hành có nhiều trạng thái, chỉ được hiện thao tác hợp lệ theo trạng thái hiện tại. Số trên dashboard phải khớp với danh sách chi tiết.
- **BE1:** SAL-06, chấp thuận báo giá và tạo đơn trong một transaction, hai người bấm cùng lúc vẫn chỉ một đơn. Không thu vượt khi ghi đồng thời, đảo thu tối đa một lần.
- **BE2:** Nặng nhất. Chống trùng lịch xe khi hai điều phối giữ cùng lúc, chống tồn âm khi hai phiếu xuất đồng thời. Nếu dùng PostgreSQL, ràng buộc `EXCLUDE` trên khoảng thời gian giải quyết gọn việc trùng lịch. **Nếu trễ, BE1 nhận WHS-05** (điều chỉnh kho có MGR duyệt, giống luồng duyệt phụ phí).
- **BE3:** Token tra cứu ≥ 128 bit lưu dạng băm, không nằm trên URL; khóa tài khoản mất quyền ngay ở request kế tiếp. Kiểm thử phải bao cả trường hợp sai và đồng thời, không chỉ luồng đúng.

---

## 3. Toàn bộ 40 user story

| Mã | Story | FE | BE | Việc FE | Việc BE | Data | Điểm |
|---|---|---|---|---|---|---|---|
| PUB-01 | Xem thông tin công ty và dịch vụ | FE1 | BE3 | Trang chủ, giới thiệu, 3 trang dịch vụ, FAQ, liên hệ; responsive 390px | API đọc nội dung PUBLISHED, chặn bản nháp/ẩn | Content | 3 |
| PUB-02 | Gửi yêu cầu dịch vụ | FE1 | BE3 | Form thay đổi theo loại dịch vụ, báo lỗi từng trường | Validation theo dịch vụ, chống gửi trùng, giới hạn 5 lần/IP/10 phút, chống script | ServiceRequest (nguồn WEBSITE) | 5 |
| PUB-03 | Nhận mã và token sau khi gửi | FE1 | BE3 | Trang xác nhận, hiện token một lần | Sinh token ≥ 128 bit, lưu dạng băm | ServiceRequest.token_hash | 2 |
| PUB-04 | Tra cứu tiến độ | FE1 | BE3 | Form mã + token gửi bằng POST, không đưa token lên URL | So token băm, chặn sau 10 lần sai/IP/15 phút, chỉ trả trường BR-18 | Đọc yêu cầu, đơn, mốc vận hành | 5 |
| PUB-05 | Quản lý nội dung công khai (CMS) | FE1 | BE3 | Màn CMS, editor, preview | DRAFT/PUBLISHED/HIDDEN, làm sạch HTML, không cho ẩn cả 3 trang dịch vụ | Content + lịch sử sửa | 5 |
| SAL-01 | Hồ sơ khách hàng | FE1 | BE1 | Danh sách, chi tiết, cảnh báo trùng | Cảnh báo trùng điện thoại/email, không tự gộp, ngừng hoạt động | Customer | 3 |
| SAL-02 | Tiếp nhận và làm rõ yêu cầu | FE1 | BE1 | Hộp yêu cầu có lọc, tìm, phân trang | State machine yêu cầu, gắn khách ACTIVE, thu hồi/cấp lại token | ServiceRequest, phần bổ sung | 5 |
| SAL-03 | Phân công và nhật ký trao đổi | FE1 | BE1 | Gán phụ trách, timeline trao đổi | Chỉ gán SAL đang ACTIVE, nhật ký chỉ thêm không sửa | ContactLog | 3 |
| SAL-04 | Lập và kiểm tra báo giá | FE1 | BE1 | Editor báo giá, dòng phí, tải PDF | Tính tiền theo BR-03 bằng số nguyên VND, phát hành sang SENT | Quote, QuoteLine | 5 |
| SAL-05 | Phiên bản và phản hồi báo giá | FE1 | BE1 | Lịch sử phiên bản, ghi phản hồi kèm tệp | SUPERSEDED, EXPIRED tính khi đọc, chặn chấp thuận bản cũ | Quote.version, bằng chứng | 5 |
| SAL-06 | Xác nhận báo giá và tạo đơn | FE1 | BE1 | Nút xác nhận, nhập hạn thanh toán | Một transaction: ACCEPTED + đúng một đơn + bản ghi vận hành | ServiceOrder + snapshot; khởi tạo chuyến/thuê/lô | 8 |
| SAL-07 | Theo dõi và cập nhật đơn | FE1 | BE1 | Danh sách, chi tiết đơn, trạng thái chung và chi tiết | Kiểm soát xung đột khi hai người cùng sửa | ServiceOrder.version | 3 |
| SAL-08 | Ghi nhận và duyệt phụ phí | FE1 | BE1 | Form đề nghị, hàng đợi duyệt của MGR | PENDING → APPROVED/REJECTED, duyệt lặp không cộng hai lần | Charge | 5 |
| SAL-09 | Hủy đơn trước thực hiện | FE1 | BE1 | Màn xác nhận hủy kèm lý do | Kiểm tra BR-15, hủy và gọi engine giải phóng lịch trong một transaction | ServiceOrder, ResourceBooking | 5 |
| TRN-01 | Quản lý xe và tài xế | FE2 | BE2 | Danh sách, chi tiết xe và tài xế | Chuẩn hóa biển số, chặn ngừng xe khi còn lịch tương lai | Vehicle, Driver | 5 |
| TRN-02 | Điều phối và đổi phân công | FE2 | BE2 | Màn phân công, lịch nguồn lực theo ngày | Engine kiểm tra trùng lịch [bắt đầu, kết thúc), tải trọng; chống giữ đôi | ResourceBooking (chung với thuê xe) | 8 |
| TRN-03 | Ghi nhận nhận hàng và vận chuyển | FE2 | BE2 | Cập nhật mốc PICKED_UP, IN_TRANSIT | Kiểm tra BR-09, vượt tải thì không nhận, lưu giờ thực tế và giờ nhập | TransportTrip, mốc vận hành | 5 |
| TRN-04 | Xác nhận giao hàng và hoàn tất | FE2 | BE2 | Form giao hàng, tải bằng chứng | Bắt buộc ít nhất một tệp, hoàn tất thì giải phóng xe và đơn COMPLETED | TransportTrip, Attachment | 5 |
| TRN-05 | Sự cố và chậm tiến độ | FE2 | BE2 | Nhật ký sự cố, cờ quá hạn | Gia hạn thì kiểm tra lại lịch, báo các booking xung đột | Incident | 5 |
| RNT-01 | Tra cứu và giữ xe | FE2 | BE2 | Lọc xe theo loại, tải trọng, khung giờ | Dùng engine booking, chống giữ đôi | ResourceBooking, RentalRecord | 5 |
| RNT-02 | Đổi hoặc bỏ giữ xe | FE2 | BE2 | Đổi xe/khung giờ kèm lý do | Đổi thất bại thì giữ nguyên booking cũ | ResourceBooking | 3 |
| RNT-03 | Lập biên bản bàn giao xe | FE2 | BE2 | Form km, nhiên liệu, phụ kiện, ảnh | Chỉ từ RESERVED, biên bản đã xác nhận không sửa | RentalRecord (bàn giao) | 5 |
| RNT-04 | Ghi nhận trả xe | FE2 | BE2 | Form trả xe, hiện thời gian trễ | Km trả ≥ km giao, cờ quá hạn, giải phóng xe | RentalRecord (nhận lại) | 5 |
| RNT-05 | Hoàn tất hồ sơ thuê | FE2 | BE2 | Nút hoàn tất, xem phụ phí | Đủ hai biên bản, không còn phụ phí PENDING | RentalRecord | 3 |
| WHS-01 | Tạo lô hàng từ đơn kho | FE2 | BE2 | Màn lô và dòng hàng | Một đơn một lô, khóa đơn vị sau khi có giao dịch | WarehouseLot, LotLine | 3 |
| WHS-02 | Lập và xác nhận phiếu nhập | FE2 | BE2 | Lập, xác nhận phiếu nhập | POSTED tăng tồn đúng một lần, nhập đầu tiên đổi trạng thái đơn | WarehouseDocument (nhập) | 5 |
| WHS-03 | Phiếu xuất từng phần | FE2 | BE2 | Lập nháp, hiện tồn hiện tại | Chống tồn âm khi hai phiếu xuất đồng thời (khóa dòng) | WarehouseDocument (xuất) | 8 |
| WHS-04 | Tra cứu tồn và liên kết vận tải | FE2 | BE2 | Bảng tồn, tuổi lô, nhật ký chứng từ | Chỉ liên kết đơn vận tải cùng khách, điểm lấy là kho | Tồn = tổng nhập − tổng xuất đã xác nhận | 5 |
| WHS-05 | Điều chỉnh chứng từ đã xác nhận | FE2 | BE2 | Đề nghị điều chỉnh, hàng đợi duyệt MGR | BR-12: không vượt lượng nguồn, kiểm tra tồn khi duyệt | WarehouseDocument (điều chỉnh) liên kết nguồn | 8 |
| WHS-06 | Kết thúc dịch vụ kho | FE2 | BE2 | Danh sách việc còn tồn đọng | Tồn bằng 0, không còn nháp/điều chỉnh/phụ phí chờ | Lô và đơn COMPLETED | 3 |
| PAY-01 | Xác định khoản phải thu | FE2 | BE1 | Chi tiết công nợ của đơn | Hàm tính BR-13 dùng chung | Đọc Order, Charge, Payment | 3 |
| PAY-02 | Ghi nhận thu tiền | FE2 | BE1 | Form thu: số tiền, phương thức, tham chiếu | Không thu vượt khi đồng thời, chặn trùng tham chiếu chuyển khoản | Payment | 5 |
| PAY-03 | Đảo khoản thu nhập sai | FE2 | BE1 | Nút đảo kèm lý do | Đảo toàn phần, mỗi khoản tối đa một lần | Payment (bản đảo liên kết nguồn) | 5 |
| PAY-04 | Tra cứu công nợ và lịch sử | FE2 | BE1 | Danh sách công nợ, lọc, cờ quá hạn | Quá hạn theo ngày Asia/Ho_Chi_Minh, tổng theo bộ lọc | Truy vấn tổng hợp, index | 5 |
| ADM-01 | Đăng nhập và quản lý phiên | FE1 | BE3 | Đăng nhập, đổi mật khẩu lần đầu | Băm mật khẩu, khóa sau 5 lần sai/15 phút, hết phiên 30 phút, thu hồi phiên ngay | User, Session | 5 |
| ADM-02 | Tài khoản và phân quyền | FE1 | BE3 | Quản lý tài khoản, gán vai trò | Middleware RBAC cho mọi API, không khóa ADM cuối cùng | User, Role, UserRole | 5 |
| ADM-03 | Danh mục dùng chung | FE1 | BE3 | Thêm, sửa, ngừng dùng loại xe và đơn vị | Ngừng dùng thay vì xóa, giữ snapshot cũ | VehicleType, Unit, thông tin kho | 3 |
| ADM-04 | Tra cứu lịch sử thao tác | FE1 | BE3 | Màn tra cứu audit | Helper ghi audit dùng chung, không có API sửa/xóa | AuditLog (chỉ thêm) | 5 |
| ADM-05 | Dashboard vận hành và tài chính | FE2 | FE2 | Dashboard theo vai trò, bấm chỉ số mở danh sách | Chỉ số snapshot và theo khoảng ngày, không đếm đôi, lọc theo quyền | Truy vấn tổng hợp | 8 |
| ADM-06 | Xuất danh sách báo cáo CSV *(Should)* | FE2 | FE2 | Nút xuất theo bộ lọc hiện tại | Chống CSV injection, ghi audit thao tác xuất | — | 3 |

---

## 4. Thứ tự làm và điểm phụ thuộc

Chuỗi nghiệp vụ: website → yêu cầu → báo giá → đơn → vận hành (vận tải/thuê/kho) → phụ phí → thu tiền. FE phụ thuộc API của BE, nên tuần đầu phải chốt hợp đồng API và dữ liệu giả.

### Giai đoạn 0 · Nền tảng
- **BE1:** ERD đầy đủ, quy ước migration, helper chống gửi trùng + version
- **BE3:** đăng nhập, middleware RBAC, helper audit, upload tệp; viết kế hoạch kiểm thử
- **BE2:** API engine giữ lịch xe (kiểm tra, giữ, giải phóng)
- **Ba BE:** chốt tài liệu API (OpenAPI) cho các màn đầu tiên
- **FE1:** khung giao diện, bộ component chung, API giả, CI/CD
- **FE2:** dựng các màn vận hành đầu tiên trên API giả
- Seed sẵn đơn CONFIRMED cho cả 3 dịch vụ

### Giai đoạn 1 · Làm song song
- FE làm trên API giả, chuyển sang API thật khi BE xong từng phần
- BE dùng đơn seed để không phải chờ SAL-06
- BE3 viết test case cho NEG và E2E song song với dev

### Giai đoạn 2 · Tích hợp
- Nối 3 luồng E2E bằng dữ liệu thật: E2E-01 vận tải, E2E-02 thuê xe trả muộn, E2E-03 kho nối vận tải
- BE3 chạy E2E, ghi bug; người sở hữu story sửa

### Giai đoạn 3 · Nghiệm thu
- **BE3:** đo hiệu năng NFR-04, kiểm thử bảo mật, báo cáo UAT
- **FE1:** thử sao lưu và khôi phục, deploy release tag lên môi trường UAT
- **FE2:** nạp bộ dữ liệu UAT (tài liệu 08 mục 2)

---

## 5. Kiểm thử ngoại lệ (NEG)

BE3 viết test case và chạy toàn bộ. Cột "Sửa lỗi" là người sở hữu code liên quan.

| Test NEG | Nội dung chính | Sửa lỗi |
|---|---|---|
| NEG-01, 02, 21, 22, 23 | Form thiếu dữ liệu, token sai, API sai quyền, khóa phiên, nội dung nháp và tệp sai loại | BE3, FE1 |
| NEG-03, 04, 05, 16, 17, 18, 19, 24 | Trùng khách, phiên bản báo giá, tạo đơn đồng thời, phụ phí, thu vượt, đảo thu, hủy đơn, xung đột khi sửa | BE1, FE1/FE2 |
| NEG-06 → 15 | Trùng lịch xe, xe không đủ điều kiện, đổi lịch, giao hàng sai trạng thái, km trả sai, xuất cạnh tranh, điều chỉnh vượt nguồn, đóng lô sớm | BE2, FE2 |
| NEG-20 | Quá hạn thanh toán và số trên dashboard | BE1, FE2 |
