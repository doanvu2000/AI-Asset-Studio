## WP-16FIX: Sửa lỗi QA lượt 16 (docs/qa/r16/QA/RESULT.md, .planning/wp/WP-16QA-report.md)
- F2 (app): FlowStartView prefill hash rác → toast "Không nạp được tệp gửi sang: invalid blob hash …" lộ tiếng Anh. Muốn toast VN gọn ("Tệp gửi sang không còn trong kho") — tái dùng cách dịch lỗi có sẵn (grep BundleError code / errorText trong app) nếu có; áp cả nơi khác dùng useQueryPrefill (sendTo.ts).
- F3 (core+app): Audit Ghi sau Xem trước lập kế hoạch lại 40–57 s (335 MB). Muốn: dry_run trả `plan_id` (cache plan trong RAM có TTL ~10 phút + fingerprint mtime/size các file trong plan); POST /api/audit/fix với `plan_id` hợp lệ và file chưa đổi → dùng lại plan, bỏ bước lập. File đổi → lập lại như cũ. Áp cho Unity/Godot và Flutter. AuditView gửi plan_id khi bấm Ghi. Test: lần 2 không gọi hàm lập plan (mock/spy), file đổi thì lập lại.
- F4 (core): tên resource Android ảnh tĩnh lặp `<asset>_<asset>` (store/pack.py + bundle.still_images): khi asset chỉ có 1 ảnh tĩnh, tên = asset slug, không ghép stem; nhiều ảnh thì `<asset>_<stem>` (stem khác asset). Kiểm iOS giữ sạch. Test.
- Phạm vi: app views/FlowStartView.vue, composables/sendTo.ts, views/AuditView.vue, api/auditTypes.ts; core export/audit.py, audit_flutter.py, api/audit.py, store/pack.py, store/bundle.py, tests. Cấm hot file api/types.ts, client.ts, mock/server.ts, mcp/server.py → báo cáo (nếu MCP audit_fix nên nhận plan_id, ghi patch).
- Model: sonnet. Không build. pytest song song, typecheck.
- Nghiệm thu: test F3, F4 fail trước pass sau; full pytest ≥ 1850 không giảm; typecheck sạch.
- Nhìn thấy được: không bắt buộc.
- Báo cáo: `.planning/wp/WP-16FIX-report.md`
## Nhật ký
- F2 xong: sendTo.ts sentFileError + SentFileMismatch, FlowStartView toast VN "Tệp gửi sang không còn trong kho".
- F3 xong: export/audit_plans.py (PlanCache TTL 600s, 8 entry, khoá, fingerprint mtime/size + report), au.make_plan/plan_paths, apply_fixes(plan=), api/audit.py plan_id; test_audit_plan_cache.py 26 audit test pass.
- F4 xong: pack_mobile.AndroidPack không nhân đôi tiền tố khi tên = asset; bundle.still_name; test_still_names.py, sửa kỳ vọng icon_icon->icon trong test_pack_mobile.
