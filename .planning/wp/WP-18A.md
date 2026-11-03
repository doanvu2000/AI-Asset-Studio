## WP-18A: Xem trước sửa Flutter nhanh hơn
- Hiện: QA r17 đo Xem trước 25–28 s / 349 MB Flutter, Ghi 39 s có plan_id (docs/qa/r17/QA/RESULT.md mục 1). Nghẽn ở encode WebP trong dry_run (core/studio_core/export/audit_flutter.py, audit_plans.py).
- Mục tiêu: dry_run không encode toàn bộ; ước lượng bytes WebP (vd encode bản thu nhỏ mẫu rồi nhân tỉ lệ, hoặc encode song song có giới hạn ≤ cpu_count-1) — chọn cách chính xác ±25% so với thật; trả `estimated: true` cho số liệu; Ghi encode thật (song song an toàn: ghi file tuần tự, encode song song). Huỷ vẫn giữa đơn vị.
- Phạm vi: core export/audit_flutter.py, audit_plans.py, api/audit.py, tests. App: AuditView chỉ thêm chữ "ước tính" nếu estimated (1–2 dòng).
- Model: sonnet. Không build. pytest song song.
- Nghiệm thu: test ước lượng ±25% trên fixture ảnh thật (vài cỡ); test huỷ; benchmark trong báo cáo (project tạm ~300 MB tự sinh trong scratch, xoá sau): Xem trước trước/sau, Ghi trước/sau; full pytest không giảm (1858).
- Báo cáo: `.planning/wp/WP-18A-report.md`
## Nhật ký
- 15:09 AUDIT: encode toàn ảnh trong plan_fixes + _run_unit tuần tự; thêm estimate_webp (tile full-res, >2MP), pool ≤cpu-1 cho plan và Ghi, flag estimated; test cũ 31 pass
- 15:13 REVIEW/bench: tile 128x64 chọn theo acc2 (lệch ≤±15% trừ sheet lặp giả), zip STORED cho ảnh; bench 230MB: Xem trước 49.5→~7s, Ghi 57.8→12.7s
- 15:18 test mới test_audit_flutter_estimate.py (13), tile 192x48, AuditView '(ước tính)' + auditTypes.estimated; typecheck chạy
- 15:21 full pytest 1880 pass, 1 fail test_p4b_mcp stdio (flaky khi tải song song, pass lẻ); scratch xoá. DONE
