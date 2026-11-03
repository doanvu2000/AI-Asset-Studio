## WP-17D — DONE
- Đã đổi: core export/audit_flutter.py (font_missing error, font_undeclared warn + fix `flutter_font` = declare_font, audio_big cho .wav > 1 MB chỉ báo, font không còn lọt vào asset_undeclared; _entries/_font_assets chịu fonts: null / [] / family thiếu fonts / packages/...), api/audit.py (Literal thêm flutter_font), tests/test_audit_flutter.py (+5 test), app AuditView.vue (nhãn VN), api/auditTypes.ts.
- Tiêu chí: 1 font thiếu file, 2 font chưa khai báo, 1 wav nặng bắt đủ; fix font đi qua plan cache (plan_id) + backup; undo khớp snapshot hash; audio không dùng nằm ở mục "Không ai dùng" (kind audio).
- Review: pubspec fonts rỗng (null, [] một dòng -> thay dòng), family đã có -> chèn thêm vào family, weight/style suy từ tên (Bold/SemiBold/Italic...), CRLF giữ, inline [..] có nội dung -> từ chối, font `packages/...` bỏ qua, tên lạ -> family = cả tên file, `[wght]` bị bỏ.
- Test: pytest test_audit_flutter + test_audit + test_audit_plan_cache = 31 passed; npm run typecheck sạch.
- Hot file cần thêm: mcp/server.py audit_project/audit_fix: mô tả thêm `flutter_font {rel}` (khai báo file font vào flutter: fonts:); findings mới font_missing/font_undeclared/audio_big (không cần code, chỉ mô tả). api/mock/server.ts không cần.
- Commit: feat(audit): Flutter lint for fonts (missing file, undeclared font with one-click declare + undo) and heavy WAV warning
