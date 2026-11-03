## WP-15B — DONE
- Đã đổi: export/audit.py (gpu_before/after theo file thực ghi, list `done`); api/audit.py (huỷ -> trả ngay, không quét lại, thêm `rescan_needed`); tests/test_audit.py +2 test.
- Tiêu chí 2/2: (a) huỷ <1 s không gọi scan; (b) tổng backup = file đã ghi.
- Audit: Flutter đã đúng (bytes theo `done`), không đổi. Lỗi nằm ở nhánh Unity/Godot (gpu totals theo todo, backup list hiển thị gpu).
- Test: audit+flutter 22 pass; full 1820 pass, 6 skip, 1 fail test_p4b_mcp::test_stdio_spawns_and_stops_core (spawn stdio, ngoài phạm vi).
- Hot file/UI cần: AuditView.vue khi response có cancelled: gọi lại POST /api/audit nếu rescan_needed=true (hash importer trong report đã cũ), tắt thanh "Đang chuẩn bị…".
- Commit: fix(audit): cancelled fix returns immediately without re-scanning and backup totals count only written files
