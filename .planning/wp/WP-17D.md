## WP-17D: Flutter lint — font + âm thanh
- Mục tiêu (core/studio_core/export/audit_flutter.py, mẫu finding/fix sẵn có f44a801):
  1. pubspec `flutter: fonts:` — family có `asset:` trỏ file không tồn tại (lỗi); file .ttf/.otf trong thư mục fonts/ hoặc assets/ không khai báo ở fonts (cảnh báo; fix = thêm family theo tên file, weight/style suy từ tên Bold/Italic nếu rõ, giữ format pubspec như flutter_declare).
  2. Âm thanh: .wav > ngưỡng (vd 1 MB) → cảnh báo đề xuất .ogg/.m4a (chỉ báo, không tự đổi); file âm thanh không dùng trong lib/ → gộp vào mục "Không ai dùng".
  3. Undo cho fix font như các fix pubspec khác.
- UI: AuditView hiện tên/mô tả VN cho finding mới (thêm vào map nhãn có sẵn, api/auditTypes.ts nếu cần type).
- Phạm vi: core export/audit_flutter.py, api/audit.py (nếu fix type mới), tests/test_audit_flutter.py; app views/AuditView.vue (chỉ map nhãn), api/auditTypes.ts. Cấm: hot file, mcp/server.py (ghi mô tả fix mới cho main gộp).
- Model: sonnet. Không build. pytest + typecheck.
- Nghiệm thu: fixture thêm 1 font thiếu file, 1 font chưa khai báo, 1 wav nặng → bắt đủ, fix font + undo hash khớp; test cũ pass.
- Báo cáo: `.planning/wp/WP-17D-report.md`
## Nhật ký
- audit_flutter.py: font_missing/font_undeclared/audio_big + fix flutter_font (declare_font) — xong
- api/audit.py Literal + AuditView nhãn + auditTypes: xong. Test: 31 passed (flutter+audit+plan_cache); typecheck sạch.
