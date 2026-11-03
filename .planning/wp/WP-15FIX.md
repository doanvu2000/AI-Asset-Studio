## WP-15FIX: Sửa lỗi QA lượt 15 (docs/qa/r15/QA/RESULT.md, .planning/wp/WP-15QA2-report.md)
- L1 (core): store/pack.py ~974 — mọi asset bị skip cho android/ios (vd chỉ PNG nhập) thì thư mục work không được tạo → 500 FileNotFoundError provenance.json. Muốn: pack vẫn ra (đích đó chỉ có warnings) hoặc lỗi có code rõ (dịch VN ở UI như nothing_to_export lượt 12). Ưu tiên: PNG nhập xuất được cho android/ios nếu exporter hỗ trợ ảnh tĩnh (xem android/ios applies_to — vì sao bị skip?). Test fail trước, pass sau.
- L2 (app): GalleryView.vue ~462-482 — query `focus=<version>` không chọn/cuộn tới bản đó. Muốn: cuộn tới + nhấn mạnh ngắn + chọn; không thấy (khác trang/lọc) → mở lightbox bản đó hoặc toast VN.
- L3 (app): CommandPalette.vue ~63-71 — "Chạy lại lượt gần nhất" lấy runList[0] không lọc; chọn run gần nhất đã kết thúc; lỗi 409 → toast VN.
- L4 (app): 18× "ResizeObserver loop" ở #/gallery — tìm nguồn, gom resize qua rAF như PageHeader (WP-14A).
- L5 (app): AuditView.vue ~190 — huỷ sớm hiện "0 file chưa sửa" (đúng 122): đếm theo plan − written.
- UX: Xem trước sửa nhiều ảnh nặng (~75 s chỉ spinner) → tiến độ qua scan.progress (kind audit_fix) cho dry_run (core export/audit.py / audit_flutter.py tick) + ScanProgressBar.
- Phạm vi: core store/pack.py (+ export android/ios nếu cần), export/audit*.py, api/audit.py, test core; app GalleryView.vue, CommandPalette.vue, AuditView.vue, component gây RO. Hot file api/types.ts, client.ts, mock → ghi "Hot file cần thêm".
- Model: sonnet. Không build. pytest song song, typecheck. Được chạy Electron thật để chụp sau sửa (docs/qa/r15/FIX/).
- Nghiệm thu: L1 test + pack PNG nhập android/ios không 500; L2–L5 có ảnh sau sửa; RO Gallery 0 sau resize; full pytest ≥ 1828; typecheck sạch.
- Báo cáo: `.planning/wp/WP-15FIX-report.md`
## Nhật ký
- [L1] pack.py: mkdir work đầu pack + warning khi asset không engine-ready bị skip android/ios; test test_pack_android_ios_only_unexportable_assets fail(500)→pass; PNG nhập vẫn không xuất được (exporter cần sheet/textures)
- [L2] GalleryView: watch focus thêm immediate (prefill chạy trước watch khi mount mới) + chờ width>0 + flash 1.8s; [L4] bỏ useElementSize, observeRect rAF cho cả virtualizer.observeElementRect
- [L3] CommandPalette: chọn lượt gần nhất đã kết thúc (RUN_ACTIVE) + 409 -> toast VN
- [L5] AuditView: left = skipped || pickedCount - written
- [UX] core audit_flutter.apply + api/audit.py: dry_run mở job audit_fix + tick plan (huỷ được); app: ScanProgressBar phase plan, AuditView preview hiện thanh + xử lý cancelled; test_dry_run_reports_plan_progress_and_can_cancel; typecheck sạch
- [done] report ghi
