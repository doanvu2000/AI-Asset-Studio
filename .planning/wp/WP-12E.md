## WP-12E: Tự xuất vào project game khi duyệt (audit W6)
- Mục tiêu: project có tuỳ chọn "Tự xuất khi duyệt": engine đích (flutter|android|ios|unity|godot — tái dùng đúng đường xuất hiện có của ExportToEngine / export_to_engine MCP) + thư mục project game đã lưu. Khi version của project chuyển approved (API approve, gate approve, MCP set_version_status) → xuất version đó vào thư mục (chạy nền, không chặn request), ghi kết quả (đường dẫn/lỗi) vào event để app hiện toast "Đã xuất vào …" / lỗi. Lỗi xuất không làm hỏng approve. Tắt mặc định.
- Phạm vi: core: store (cột/settings project — migration idempotent), api project update + nơi approve version (routes/hook chung, tìm 1 điểm chung thay vì rải), module mới core/studio_core/engine/auto_export.py (hoặc tên hợp), tests/test_auto_export.py; app: views/ProjectDetailView.vue (khối cài đặt), components/ExportToEngine.vue (tái dùng chọn engine/thư mục nếu được), toast lắng nghe event (store events có sẵn — tìm nơi toast workflow-run F3 làm mẫu). | Cấm: api/types.ts + api/client.ts (ghi patch "Hot file cần thêm", main gộp), mcp/server.py, RunDetailView.vue, NewRunView.vue, PackDialog.vue, AuditView/RestyleView/PageHeader/App.vue/AppSidebar (WP-12U1), ops/lut.py (WP-12P1).
- Model: sonnet. Skill: flutter-tips-tricks không cần; đọc README export.
- Tiêu chí:
  - [ ] pytest: bật + approve → file xuất có trong thư mục đích (flutter ít nhất + 1 đích khác); tắt → không xuất; thư mục không tồn tại/không ghi được → approve vẫn OK, event lỗi; approve lặp không nhân bản file (ghi đè ổn định).
  - [ ] Migration chạy 2 lần OK.
  - [ ] `cd app && npm run typecheck` sạch (sau khi main gộp types — nếu cần types mới, tạm khai báo cục bộ và ghi patch).
  - [ ] Ảnh mock ProjectDetail khối "Tự xuất khi duyệt" light/dark → docs/qa/r12/WP-12E/ (VITE_MOCK=1; mẫu script: C:/Users/dauph/AppData/Local/Temp/claude/L--AI-AI-Asset-Studio/0bc24b1f-b9c5-491c-b6e6-10c0513fb320/scratchpad/qa/lib.mjs). Mock server: thêm field project tương ứng (api/mock/server.ts được phép sửa phần project).
- Nhìn thấy được: có.
- Báo cáo: `.planning/wp/WP-12E-report.md`
## Nhật ký
- core: module auto_export.py (bus listener version.updated), cột projects.auto_export/_dir, PATCH field, start trong lifespan
- pytest test_auto_export.py 7 test xanh; bắt đầu UI
- app: AutoExportRow.vue + composables/autoExport.ts + events.ts toast + mock patch; tiếp typecheck + ảnh
- DONE/PARTIAL: report ghi, pytest full 1688 pass, typecheck sạch, ảnh xong (thiếu toast)
