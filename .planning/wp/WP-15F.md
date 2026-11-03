## WP-15F: Flutter project lint + sửa 1 chạm
- Mục tiêu: Audit quét được project Flutter có sẵn (thư mục có pubspec.yaml), báo + sửa:
  1. asset khai báo trong pubspec `flutter: assets:` nhưng file/thư mục không tồn tại (lỗi) và file trong assets/ chưa khai báo (cảnh báo; sửa = thêm vào pubspec, giữ nguyên format/comment còn lại của file, sửa tối thiểu dòng).
  2. Biến thể độ phân giải: có `2.0x/` mà thiếu `3.0x/` (hoặc ngược lại) cho cùng tên → cảnh báo; sửa = sinh biến thể thiếu bằng resize Lanczos từ bản lớn nhất (không upscale quá bản gốc).
  3. PNG/JPG lớn (> ngưỡng bytes hoặc cạnh > 2048, tái dùng ngưỡng mobile có sẵn trong export/mobile.py nếu hợp) → sửa = chuyển WebP (lossless nếu có alpha cứng, quality 90 nếu ảnh) + cập nhật tham chiếu chuỗi trong lib/**/*.dart và pubspec.
  4. Asset không được tham chiếu trong lib/**/*.dart (so chuỗi đường dẫn và tên file) → cảnh báo (không tự xoá; sửa = không có).
- Tái dùng: export/audit.py (khung finding/fix/undo, nhận diện engine Unity/Godot), audit_media.py, scan_jobs (tiến độ + huỷ an toàn giữa đơn vị), endpoint audit hiện có. Engine detect thêm "flutter". Undo phải trả cả pubspec.yaml và file .dart về như cũ.
- UI: AuditView hiện engine Flutter (nhãn VN, finding mới có tên/mô tả VN). Tái dùng component có sẵn, không viết lại view.
- Phạm vi: core/studio_core/export/audit*.py hoặc file mới export/audit_flutter.py, api route audit nếu cần field mới, app/src/renderer/src/views/AuditView.vue + component audit, test core/tests/test_audit_flutter.py (fixture project Flutter tạo trong tmp_path có đủ lỗi cài sẵn).
- Cấm: hot file app api/types.ts, api/client.ts, api/mock/server.ts, core mcp/server.py → ghi "Hot file cần thêm". Không chạm export/android_fx*, ios_fx*, shader*.
- Model: sonnet. Không build. Được chạy pytest + `cd app && npm run typecheck`.
- Nghiệm thu: fixture có ≥ 8 lỗi cài sẵn (2 thiếu file, 2 chưa khai báo, 1 thiếu 3.0x, 2 ảnh to, 1 không dùng) → bắt đủ; sửa xong quét lại còn đúng các finding không tự sửa được; dung lượng ảnh giảm; undo khôi phục hash mọi file; huỷ giữa chừng không để file dở; Unity/Godot audit test cũ không đổi; full pytest không giảm; typecheck sạch.
- Nhìn thấy được: có (AuditView với project Flutter) — được chạy app (xem cách chạy .planning/wp/WP-13QA.md nhật ký) hoặc ghi CHƯA NHÌN.
- Báo cáo: `.planning/wp/WP-15F-report.md`
## Nhật ký
- [audit] Đọc brief + audit.py/api/audit.py/AuditView/auditTypes. Kế hoạch: file mới export/audit_flutter.py (scan + fix + undo), audit.py chỉ dispatch (detect_engine, scan/apply_fixes/undo), api/audit.py thêm engine flutter + 3 loại fix, UI thêm tab "Vấn đề Flutter" trong AuditView, types ở api/auditTypes.ts (không phải hot file).
- [code] Viết export/audit_flutter.py + dispatch audit.py + api/audit.py + tests/test_audit_flutter.py; chạy test lần 1
- [test] Lần 1: 11/11 pass (test_audit_flutter.py); đổi WebP method=4 cho nhanh. Bắt đầu UI AuditView + auditTypes.ts
- [ui] AuditView + auditTypes.ts: engine Flutter, tab Vấn đề (findings), tile Flutter, confirm bytes; typecheck sạch
- [test] Full pytest: 1818 passed, 6 skipped (92 s); typecheck sạch. Chờ cổng 5173 (agent khác đang chạy dev) để chụp UI
- [nhìn] Chụp AuditView Flutter thật (Electron cổng 5199/CDP 9345, data tạm, tắt theo PID): docs/qa/r15/WP-15F/01..04; sửa: tile dung lượng đếm .webp, variant webp, undo dọn thư mục 2.0x rỗng, toast 'mục sửa'
