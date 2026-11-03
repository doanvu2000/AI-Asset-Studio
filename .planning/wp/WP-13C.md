## WP-13C: Tiến độ trực tiếp cho quét Audit/Restyle + chấm lại QA Gallery
- Mục tiêu: hiện API audit / restyle scan là 1 lời gọi dài không tiến độ; Gallery recompute QA chunk 500 chỉ toast cuối. Thêm tiến độ qua bus event có sẵn (WS events, như step.progress): core phát `scan.progress {job, done, total, phase}` trong audit/restyle scan + recompute QA; app hiện "Đã quét N/M tệp" + thanh tiến độ trong EmptyState scanning (AuditView/RestyleView) và thanh nhỏ ở Gallery khi chấm lại; nút Huỷ (endpoint huỷ hoặc AbortSignal → core dừng giữa chừng an toàn).
- Phạm vi: core module audit/restyle scan + nơi recompute QA (tìm: export/audit.py, api tương ứng, restyle scan), core/tests mới; app views/AuditView.vue, views/RestyleView.vue, views/GalleryView.vue, stores/events.ts (chỉ thêm handler), composable mới nếu cần. | Cấm: api/types.ts + api/client.ts (ghi patch: EventType mới, chữ ký huỷ), api/mock/server.ts chỉ thêm phát progress cho audit/restyle mock, mcp/**, FlowCanvas/StepNode/RunsView (WP-13A), adapters/normal_ai* (WP-13B).
- Skill: web-design. Model: sonnet.
- Tiêu chí: pytest: event progress phát đúng thứ tự, total đúng, huỷ dừng sớm không hỏng dữ liệu; typecheck sạch; ảnh mock audit/restyle đang quét có N/M + thanh, Gallery chấm lại có thanh (light+dark) → docs/qa/r13/WP-13C/.
- Nhìn thấy được: có. Báo cáo: `.planning/wp/WP-13C-report.md`
## Nhật ký
- [core] scan_jobs.py + audit/restyle/qa wired + POST /api/scans/cancel; tests/test_scan_progress.py (7) + audit/restyle/qa suites 52 pass
- [app] scanProgress.ts, ScanProgressBar.vue, events.ts handler, Audit/Restyle/Gallery wired
- [test] mock + Playwright 1280 light/dark: audit/restyle/gallery progress + cancel OK, scrollWidth=innerWidth; ảnh docs/qa/r13/WP-13C
- [report] WP-13C-report.md xong, vite 5302 đã tắt
