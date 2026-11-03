## WP-12QA: QA Electron thật + core thật cho lượt 12
- Mục tiêu: chạy app thật (KHÔNG mock) với core thật, data dir tạm, kiểm từng tính năng lượt 12, chụp ảnh, ghi ĐẠT/LỖI từng mục. Không sửa code (chỉ ghi lỗi + file:line nghi ngờ).
- Cách chạy (handoff cũ): `cd app && STUDIO_DATA_DIR=<dir tạm trên L: hoặc scratchpad> npx electron-vite dev -- --remote-debugging-port=9334` + Playwright connectOverCDP (playwright-core có sẵn trong scratchpad các WP trước; Edge). Không GPU/ComfyUI: chỉ dùng recipe CPU (vd sprite-normal-map, các recipe CPU khác) hoặc import ảnh có sẵn (import_to_library) để có version.
- Mục kiểm:
  1. Settings → pack "normal-ai" hiện, trạng thái (máy này có L:\AI\setup\normal-ai → đã cài), đèn hợp máy.
  2. NewRun sprite-normal-map: method trong nâng cao; chạy 1 ảnh method=ai → output normal; thời gian thật.
  3. RunDetail: nút Chạy lại (seed mới / giữ seed), seed trong inspector; chạy lại thật.
  4. NewRun: lưu thiết lập, áp lại, xoá; chế độ 1 prompt × N seed (với recipe CPU có seed nếu có; không có thì ghi CHƯA KIỂM); công tắc auto-pick hiện.
  5. ProjectDetail: bật "Tự xuất khi duyệt" + thư mục tạm → duyệt 1 version → file xuất có trong thư mục + toast "Đã xuất…".
  6. PackDialog: bật Theme UI, đích flutter → gói có lib/gen/app_theme.dart.
  7. Gallery lọc 1 hàng + popover; Audit/Restyle empty; Flow editor Delete/Ctrl+Z; Ctrl+Enter ở Audit.
  8. Cold-start vào thẳng `#/new` (lỗi WP-12N báo: fit.load ném "Core chưa sẵn sàng") và recipes reload reset field vừa nhập — tái hiện, ghi bước.
  9. Console renderer: đếm error; ghi lỗi.
- Bằng chứng: docs/qa/r12/QA/*.png + docs/qa/r12/QA/RESULT.md (ĐẠT/LỖI/CHƯA KIỂM từng mục, độ phân giải).
- Model: sonnet. Không sửa file nguồn. Dọn: tắt Electron/core/vite theo PID, xoá data dir tạm.
- Báo cáo: `.planning/wp/WP-12QA-report.md`
## Nhật ký
- [1] Settings normal-ai: pack 'Normal map AI' Tốt/400MB/Đã cài — ĐẠT (docs/qa/r12/QA/03_settings_normalai.png)
- [8-cold] reload #/new, #/new?recipe=..., ?mode=batch x3 mỗi URL (core đã ready): KHÔNG tái hiện lỗi fit.load (chỉ cảnh báo Electron CSP). Sẽ thử lại bằng cách kill core con.
- [2] sprite-normal-map method=ai (nằm trong nâng cao) trên ảnh 256px: step normal xong 4,7 s, meta method=ai model=Ruicheng/moge-2-vits-normal warnings=[] — ĐẠT (08_normal_run.png)
- [5a] AutoExport bật + thư mục L:\...\qa12-export trên Flutter; duyệt version (Gallery nút Duyệt) normal-map → toast LỖI "Không xuất được … : nothing to export for this target (needs a sprite sheet…)" (EN thô trong toast VN; normal-map standalone không xuất được). Pipeline event OK; thử lại bằng recipe CPU xuất được (weather-overlay).
- [3-pre] Gate approve ở RunDetail KHÔNG đổi version.status (vẫn draft) → auto-export chỉ chạy khi duyệt version ở Gallery/Dự án.
- [5] AutoExport weather-overlay (CPU) → duyệt version ở Gallery → toast 'Đã xuất … vào …\approve' + file lib/gen/*.dart, assets/weather/*, pubspec_assets.yaml, README_FLUTTER.md trong thư mục tạm — ĐẠT; LỖI nhỏ: 4 toast trùng cho 1 lần duyệt card nhiều version; thư mục đích dùng tên step 'approve' thay vì tên asset (17_autoexport_toast.png)
- [3] RunDetail: menu Tuỳ chọn chạy lại (seed mới / giữ seed), giữ seed xám khi step không seed (weather); ui-juice seed=7: inspector hiện 'Seed 7', 'giữ seed' → run mới, step cache 'Dùng lại', toast 'Đã chạy lại với seed cũ'; 'seed mới' → run mới, ĐẠT. Lưu ý: seed mới trên recipe seed tường minh (7) vẫn cache hit seed 7 (không đổi seed) (19_rerun_menu, 22_seed_inspector, 23_rerun_same)
- [4] NewRun ui-juice: batch 'Nhiều prompt' → '1 prompt × nhiều seed' (N 2–16, seed gốc): batch 5 lượt seed 7..11 tạo thật, bảng hiện seed từng lượt; preset: lưu (Mọi dự án / Chỉ dự án), liệt kê, áp (seed 9→55), xoá 2 bước 'Xoá?' — ĐẠT. Lưu ý: preset KHÔNG lưu varyBy/số lượt/seed gốc (NewRunView.vue:471 presetSnapshot) → áp preset trong chế độ seeds không đổi N/seed (quan sát, không crash). (25_batch_mode,26_vary_seeds,31_presets_list,35_preset_applied)
- [4b] Công tắc 'Tự chọn ảnh điểm QA cao nhất' hiện chỉ ở recipe có gate.pick (concept-2d-codex có, ui-juice không), bật bằng chuột thật được + cảnh báo — ĐẠT (không chạy: cần GPU). Ghi chú: Playwright locator.click trên .track không lật được input (cần mouse.click toạ độ) — nghi do input 1x1 opacity 0 absolute, kiểm chuột thật OK
- [6] PackDialog: tick 'Theme UI', đích Flutter → gói du-an-dau-tien-1.0.0/flutter/lib/gen/app_theme.dart có; dialog báo 'Bỏ qua cho Flutter: Normal_map…' + cảnh báo theme palette fallback — ĐẠT (40_pack_theme_on, 41_pack_done)
- [7a] Gallery: thanh 1 hàng (tìm + dự án + chip Lọc), popover 3 nhóm + checkbox điểm thấp, chip 'Lọc (1)' + 'Xoá lọc', về 'Lọc'; scrollWidth=innerWidth=1320 — ĐẠT (42..44)
- [7b] Audit/Restyle trạng thái rỗng đúng (45_*_empty); Ctrl+Enter ở Audit với đường dẫn Godot tạm → quét ra báo cáo 2 ảnh/1 nhóm trùng (47_audit_ctrl_enter) — ĐẠT. Ctrl+Enter khi rỗng: không làm gì (ĐẠT)
- [7c] Flow editor: thêm 2 node (đếm 3 gồm node đầu vào) → Delete (2) → Ctrl+Z (3) → Ctrl+Y (2) → undo hết về 1, undo thừa an toàn — ĐẠT (50..52)
- [8a] Cold-start #/new: TÁI HIỆN khi core chưa sẵn sàng (kill core PID con rồi goto #/new): pageerror 'Core chưa sẵn sàng' + 'TypeError: Cannot read properties of null (reading placeholder)', splash 'Core đã sẵn sàng' kẹt mãi (còn cả khi chuyển #/gallery) tới khi reload. #/, #/gallery, #/projects cold không lỗi. Nghi: stores/fit.ts:19 gọi api() đồng bộ ngoài chuỗi promise (throw trước .catch) từ NewRunView.vue:69 — LỖI (54_cold_recipe_t9, 55_cold_recipe_after)
- [8b] Recipes reload reset field: TÁI HIỆN (2/5 lần) — #/new?recipe=concept-2d-codex, gõ vào textarea in-prompt ~190-210 ms sau khi navigate → giá trị về '' sau <1,5 s; 3/5 lần giữ. Race nhẹ với người gõ tay (cửa sổ ~0,2 s sau mount) — LỖI nhẹ (có sẵn, WP-12N đã báo)
- [9] Console: 11 ERROR (7 ResizeObserver vô hại, 4 do L1). Dọn xong, WP-12QA DONE.
