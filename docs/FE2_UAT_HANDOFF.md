# FE2 — kiểm thử và bàn giao UAT

Ngày kiểm tra: 28/11/2026. Nhánh `FE2`. Đây là kiểm tra kỹ thuật; nhóm/BE3 vẫn cần review, chạy trên release candidate và ký nghiệm thu.

## Dữ liệu mẫu và cách chạy

Từ gốc `mock_project_se`:

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe scripts/prepare_fe2_uat.py
$env:BE_DB_PATH='data/fe2-uat.sqlite3'
$env:BE_FILES_DIR='data/fe2-uat-files'
.\scripts\run_fe2.ps1 -Service backend
```

Mở terminal khác: `.\scripts\run_fe2.ps1 -Service frontend`, rồi mở `http://127.0.0.1:5174/app`. Mật khẩu tài khoản mẫu: `Password@123`. Manifest `data/fe2-uat.json` ghi mã tình huống. Script chỉ làm việc với DB tên `fe2-uat*` trong `data/`, chạy lại không nhân đôi đơn mẫu. Xe/lịch liên kết kho cùng khách được chuẩn bị cho luồng WHS-04.

## Bằng chứng đã chạy

| Luồng | Bằng chứng UI trên DB riêng `data/fe2-uat-final.sqlite3` | Bằng chứng API |
|---|---|---|
| E2E-01 vận tải | `TRP-000006`: phân công → nhận → vận chuyển → giao có PNG → hoàn tất; `ORD-000005` hoàn tất | `tests/test_e2e_full_3_flows.py` |
| E2E-02 thuê xe | `RNT-000005`: giữ → bàn giao → trả → hoàn tất. `RNT-000003`: quá hạn 744 phút theo `booking.planned_end`, trả xe có PNG → đề nghị phụ phí 300.000 ₫ → API chặn hoàn tất `PENDING_CHARGES`, UI ẩn nút → `mgr1` duyệt qua API → UI hoàn tất → `fin1` thu đủ 2.500.000 ₫, số dư 0. | `tests/test_e2e_full_3_flows.py`; script UAT cho trả muộn/phụ phí/duyệt trong `scripts/` |
| E2E-03 kho | `LOT-000005`: nhập 10 → điều chỉnh -1 được MGR duyệt → thử đóng sớm bị chặn → xuất 9 liên kết `TRP-000004` → đóng khi tồn 0 | `tests/test_e2e_full_3_flows.py` |
| Thanh toán | `ORD-000008`: thu 100.000 ₫ rồi đảo, số dư về 1.100.000 ₫. `ORD-000003`: thu đủ sau phụ phí được duyệt. | `tests/test_neg_all_24.py`, `tests/test_sales.py` |
| Dashboard/CSV | FIN: chỉ số công nợ khớp danh sách 8 đơn; tải CSV công nợ. DSP: sau sửa, chỉ số “Xe thuê quá hạn” là 1 và danh sách mở ra đúng `RNT-000004` (kết thúc lịch giữ 11:18 11/28/2026). | `tests/test_fe2_reports.py` |

Vai trò đã thử: DSP, WHS, MGR, FIN. NEG-06–15, 17–18, 20–21, 24 và 3 E2E có test API trong `tests/test_neg_all_24.py`, `tests/test_e2e_full_3_flows.py`. Phiên UI dùng dữ liệu cục bộ giả lập, không phải môi trường production.

Kịch bản trả muộn có thể tái tạo trên DB UAT riêng bằng `scripts/prepare_fe2_late_uat.py`, `scripts/prepare_fe2_late_charge_uat.py`, `scripts/approve_fe2_late_charge_uat.py`. Chọn mốc giờ có múi giờ trước giờ máy hiện tại khi chuẩn bị trả muộn. Bản `fe2-uat-final.sqlite3` đã chạy hết kịch bản; tạo DB mới nếu muốn thao tác lại từ đầu. FE1 trang `/app/phu-phi` hiện chưa cung cấp thao tác đề nghị/duyệt phụ phí từ danh sách trong phiên thử; hai bước đó đã được kiểm tra qua API. Đây là điểm cần FE1/nhóm hoàn thiện nếu UAT yêu cầu làm hoàn toàn trên UI.

## Hiệu năng và bàn giao

Benchmark 100.000 đơn và 1.000.000 giao dịch kho, 10 mẫu/endpoint: p95 Dashboard FIN 1084,62 ms, SAL 1441,04 ms, danh sách công nợ 287,49 ms, quá hạn công nợ 137,17 ms, lô còn hàng 108,47 ms; dưới ngưỡng 2000 ms. Lệnh: `.\.venv\Scripts\python.exe -m scripts.benchmark_fe2_dashboard`.

Trước merge: BE2 review các sửa đổi API của mình, BE3 chạy lại NEG/E2E trên release candidate, nhóm đối chiếu bằng chứng theo tài liệu 08 và ký nghiệm thu. Không đưa DB UAT, file đính kèm, PDF/Markdown nguồn, `node_modules` hoặc `dist` vào PR.
