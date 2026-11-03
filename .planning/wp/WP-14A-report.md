## WP-14A — DONE (RO: không tái hiện trước/sau)
**Đã đổi:** FlowCanvas.vue (mũi tên chỉ chọn node trong nón ±45°, không có thì đứng yên) · core api/run_presets.py + store/repo.py (PATCH /api/presets/runs/{id}: name/inputs/params, tên trùng cùng recipe+phạm vi -> 409, rỗng -> 422, 404) · api/runPresets.ts, client.ts, mock/server.ts (updateRunPreset, đúng phần preset) · RunPresetMenu.vue (nút đổi tên inline + "Ghi đè bằng thiết lập hiện tại" bấm 2 lần xác nhận; báo trùng tên, Esc huỷ) · PageHeader.vue (ResizeObserver -> requestAnimationFrame) · tests/test_run_presets.py (+test_patch).
**Tiêu chí 3/3:** pytest preset 7 pass · typecheck sạch · Playwright mock đủ.
**Audit:** diff chỉ trong phạm vi; không đụng file WP-14B/14C.
**Review:** PageHeader.fit đổi class `compact` ngay trong callback RO = nguồn lỗi loop (RegionEditDialog/FlowCanvas chỉ set ref, không đổi layout đồng bộ nên giữ). Đổi tên về cùng tên (khác hoa thường) không bị 409. PATCH rỗng body trả preset nguyên vẹn.
**Test:** run flow 4 node dọc: ArrowRight/Left đứng yên, Up/Down đi đúng node kề (trước đây Phải nhảy xuống). Preset: lưu -> đổi tên "Rương vàng" -> ghi đè (toast) -> đổi tên trùng "rương VÀNG" nút Lưu tên disabled + cảnh báo; light+dark. Console #/gallery và #/runs/<id> (kèm resize viewport): 0 ResizeObserver error. CHƯA KIỂM: trước-sau của lỗi RO (không có bản cũ để so, cấm stash), nón ±45° với layout nhiều cột (mock chỉ có chuỗi dọc).
**Nhìn:** docs/qa/r14/WP-14A/ run_flow.png, preset_menu|rename|overwrite|clash_{light,dark}.png; đã mở xem rename_dark, clash_light: ổn.
**Hot file cần thêm:** PageHeader.vue.
**Ngoài phạm vi:** vite mock cổng 5311 đã tắt theo PID.
**Commit đề xuất:** feat(ui): rename and overwrite saved run presets via PATCH, arrow keys stay within a 45-degree cone, defer page header fit to animation frame
