## WP-12QA — DONE (QA Electron thật + core thật, 2026-11-03, cửa sổ 1320x852, sáng)
Cách chạy: `STUDIO_DATA_DIR=L:\AI\AI-Asset-Studio\scratch\qa12-data npx electron-vite dev -- --remote-debugging-port=9334 --user-data-dir=...` + Playwright connectOverCDP. Không GPU/ComfyUI. Recipe dùng: sprite-normal-map, weather-overlay, ui-juice (CPU).

### Kết quả từng mục
1. Settings pack normal-ai — ĐẠT: "Normal map AI · Tốt · 400 MB · Đã cài" (03_settings_normalai.png).
2. NewRun sprite-normal-map — ĐẠT: method (Tự chọn / AI MoGe-2 / Bộ lọc) nằm trong "Tuỳ chọn nâng cao"; chọn AI, ảnh 256 px → step normal 4,7 s, meta method=ai model=Ruicheng/moge-2-vits-normal, warnings rỗng (06..09).
3. RunDetail Chạy lại — ĐẠT: menu "seed mới/giữ seed" (19, 22b), "giữ seed" xám khi step không có seed, ui-juice hiện "Seed 7" trong inspector, giữ seed → run mới, step "Dùng lại", toast "Đã chạy lại với seed cũ" (22, 23). Lưu ý: "seed mới" với seed tường minh (7) vẫn cache seed 7.
4. NewRun preset / N seed / auto-pick — ĐẠT: "1 prompt × nhiều seed" (N 2–16, seed gốc) tạo lô 5 lượt seed 7..11 thật; lưu/áp/xoá thiết lập (xoá 2 bước "Xoá?"); công tắc auto-pick chỉ hiện ở recipe có gate.pick (38). CHƯA KIỂM: chạy auto-pick thật (cần GPU). Preset không lưu varyBy/N/seed gốc.
5. Tự xuất khi duyệt — ĐẠT: bật + thư mục tạm, duyệt version ở Gallery → toast "Đã xuất … vào …" + file lib/gen/*.dart, assets/weather/*, README_FLUTTER.md (17_autoexport_toast.png). Duyệt gate ở RunDetail KHÔNG đổi version thành approved (đúng thiết kế, chỉ Gallery). Normal-map đơn lẻ → toast lỗi EN "nothing to export for this target…".
6. PackDialog Theme UI — ĐẠT: gói flutter/lib/gen/app_theme.dart có; cảnh báo "style bible has no palette, used the neutral fallback palette" (40, 41).
7. Gallery/Audit/Restyle/Flow — ĐẠT: thanh lọc 1 hàng + popover + "Lọc (1)"/"Xoá lọc" (42–44); Audit/Restyle rỗng đúng (45); Ctrl+Enter quét ở Audit (47) và Restyle (53); Flow Delete/Ctrl+Z/Ctrl+Y/undo thừa (50–52). CHƯA KIỂM: Ctrl+Enter ở FlowStart (sẽ chạy GPU), confirm xoá node có dây, undo nối dây.
8. Cold-start / recipes reload — LỖI tái hiện được (xem dưới).
9. Console — xem dưới.

### Lỗi tìm thấy
- L1 (cao, tái hiện 2/2): cold-start #/new khi core chưa sẵn sàng. Bước: kill core con (taskkill PID), ngay lập tức mở `...#/new?recipe=sprite-normal-map` (hoặc `#/new`) → pageerror "Core chưa sẵn sàng" + "TypeError: Cannot read properties of null (reading 'placeholder')", splash "Core đã sẵn sàng" kẹt vĩnh viễn (vẫn kẹt khi chuyển #/gallery), chỉ reload khi core đã chạy mới hết. `#/`, `#/gallery`, `#/projects` cold không lỗi. Nghi: app/src/renderer/src/stores/fit.ts:19 `api()` ném đồng bộ trước chuỗi .catch, gọi từ NewRunView.vue:69 `void fit.load()`; TypeError thứ 2 là hệ quả render setup dở (54_cold_*, 55).
- L2 (thấp, 2/5 lần): recipes reload reset field. Bước: mở `#/new?recipe=concept-2d-codex`, gõ vào textarea in-prompt ~190–210 ms sau navigate → giá trị về rỗng trong <1,5 s.
- L3 (thấp): 1 lần duyệt card nhiều version (weather) ra 4 toast "Đã xuất" trùng nội dung (events.ts toast project.auto_export; engine/auto_export.py phát event mỗi version). Thư mục đích đặt theo tên step "approve" thay vì tên asset.
- L4 (thấp): toast lỗi auto-export lộ tiếng Anh thô từ core ("nothing to export for this target (needs a sprite sheet…)"; store/bundle.py:472).
- Quan sát: preset không lưu chế độ seed (NewRunView.vue:471 presetSnapshot); app tự mở dialog "Có gì mới" ở lần chạy đầu (01_settings_top.png, bình thường).

### Console errors (renderer.log, ~1 giờ phiên)
11 ERROR: 7× "ResizeObserver loop completed with undelivered notifications" (Chromium vô hại; xảy ra ở #/gallery, #/runs/<id>), 2× "Core chưa sẵn sàng" + 2× "Cannot read properties of null (reading 'placeholder')" (đều do L1, cố ý gây). Không error khác; core.log chỉ có ConnectionResetError asyncio khi renderer reload/kill (vô hại). Cảnh báo Electron CSP unsafe-eval (chỉ dev).

### Ảnh
61 ảnh docs/qa/r12/QA/*.png (01..55, 1320x852). Dọn: Electron/core/vite tắt theo PID, đã xoá scratch/qa12-*.
