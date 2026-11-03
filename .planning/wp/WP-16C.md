## WP-16C: Flow nối với phần còn lại
- Mục tiêu:
  1. FlowStartView (/flows/:id/run) nhận prefill query `input.<name>=<hash>` + `project` như NewRunView.vue:248-256 (đọc rồi router.replace xoá query).
  2. composables/sendTo.ts: thêm đích "Chạy flow…" — liệt kê flow người dùng có input nhận được loại blob đang gửi (ảnh/mesh…); chọn → /flows/:id/run với prefill.
  3. FlowRunView: output cuối có SendToMenu (như RunDetail).
  4. Ctrl+K: mục "Chạy flow <tên>" cho các flow đã lưu.
- Phạm vi: app views/FlowStartView.vue, FlowRunView.vue, composables/sendTo.ts, components/SendToMenu.vue, CommandPalette.vue. Hot file → báo cáo. Không chạm ShaderPicker (WP-16B), core.
- Model: sonnet. Không build. typecheck. Ảnh docs/qa/r16/WP-16C/ (vite mock hoặc Electron thật, cổng riêng, tắt theo PID).
- Nghiệm thu: Gallery chọn 1 ảnh → Gửi tới → Chạy flow → form flow có ảnh sẵn; FlowRun output gửi tới Restyle/Pack; palette có flow; typecheck sạch.
- Nhìn thấy được: có.
- Báo cáo: `.planning/wp/WP-16C-report.md`
## Nhật ký
- 13:43 AUDIT+code: sendTo flow target + wildcard useQueryPrefill, FlowStart prefill, FlowRun SendToMenu, palette flows; typecheck chạy
- TEST+NHÌN: vite mock cổng 5291 (đã tắt), ảnh docs/qa/r16/WP-16C/01-05; sửa bug wildcard prefill; palette bỏ hint (nhãn bị cắt). typecheck sạch.
