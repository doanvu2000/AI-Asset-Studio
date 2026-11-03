## WP-17QA — DONE (Electron thật + core thật, 2026-11-03, cửa sổ 1320x852, sáng, không GPU/ComfyUI)
Cách chạy: `electron-vite dev -c scratch/qa17-cfg/electron.vite.config.ts -- --remote-debugging-port=9362 --user-data-dir=scratch/qa17-ud`, STUDIO_DATA_DIR=scratch/qa17-data, Playwright connectOverCDP. Vite phải bind 127.0.0.1:5173 (config tạm, y hệt config thật trừ host) vì [::1]:5173 đang bị vite của project khác (Claude-Code-Monitor) chiếm; core vẫn cho phép origin 127.0.0.1:5173. Seed: dự án QA17 Game, 5 ảnh nhập thư viện, recipe thử `qa17-pick` (CPU, có gate.pick; không có recipe gate.pick CPU nào sẵn), 1 run ui-juice chờ cổng; Godot 305 MB (48 PNG 2048x1024), Flutter 349 MB (80 PNG), Flutter font/wav 2 MB. Đã tắt theo PID và xoá scratch/qa17-*.

### Kết quả từng mục
1. Audit Ghi sau Xem trước (c4e50ea plan_id) + tên resource Android — ĐẠT (đo qua API /api/audit/fix, cùng luồng UI).
   - Godot 305 MB (chỉ ghi .import): Xem trước 0,1 s; Ghi 4,4–4,5 s dù có hay không plan_id (chủ yếu là quét lại 4,4 s) nên plan_id không đổi gì ở Godot/Unity. Sửa 1 .import giữa chừng → 47 ghi + 1 skip (an toàn); undo 48/47 file, conflicts [].
   - Flutter 349 MB (80 PNG → WebP), mỗi mốc sau khi quét lại: Xem trước 25–28 s; Ghi KHÔNG plan_id 62,7 s; Ghi CÓ plan_id 39,0 s (giảm ~24 s, bỏ lập lại kế hoạch); sửa lib/main.dart giữa Xem trước và Ghi rồi Ghi có plan_id → 73,0 s (lập lại, đúng kỳ vọng). Undo mỗi lượt 161 file, conflicts [].
   - Android ảnh tĩnh: asset `icon` → `drawable-*dpi/icon.webp`, `R.drawable.icon` (AssetStudioAssets.kt:90), `icon1024` tương tự; hết icon_icon / icon256_icon256 (04_pack_done).
2. WP-17A — ĐẠT.
   - Hash rác ở #/restyle?ref= và #/new?recipe=ui-juice&input.image= → toast VN "Tệp gửi sang không còn trong kho", query bị xoá (01_toast_*).
   - PackDialog (Gallery chọn icon + icon1024 → Đóng gói): mọi dòng ảnh nhập có nhãn "chỉ Android/iOS" (03_pack_dialog).
   - 6 thumbnail shader dùng blob= của asset đầu tiên chọn (icon, vòng tròn đỏ); bỏ tick icon → đổi sang icon1024 (04_pack_sample_changes).
3. WP-17B — ĐẠT (1 chỗ CHƯA KIỂM).
   - Cổng chọn (recipe thử qa17-pick): S mở menu Gửi tới (4 Tạo mới, 3 Chạy flow, Restyle); nút "Gửi tới…" footer cùng menu; hint "S gửi tới" (05, 06). NewRun nhận ảnh ứng viên 2, Restyle nhận ứng viên đang focus, Chạy flow "Lát cắt game 1 nút" nhận ứng viên 1, project điền đúng, Back về #/gates (07–09).
   - S không kích khi gõ input/textarea, khi Ctrl+K đang mở; Ctrl+S/Alt+S không kích; 0 pageerror (10).
   - Cổng duyệt ui-juice: S + nút Gửi tới…; phím 4 mở Lightbox (juice_loop.webp 4/29) có nút Gửi tới… và phím S; Restyle từ lightbox nhận đúng ảnh (12–15). Bảng phím "?" có dòng S (22).
   - CHƯA KIỂM: Lightbox trên cổng chọn (GatePicker.vue:25,185 có `lightbox` nhưng không có chỗ gán → không mở được); "Nhập thư viện" ở cổng (17B cố ý chưa hiện).
4. WP-17C — ĐẠT: BundleMenu (RunDetail ui-juice sau Duyệt → Xuất cho engine) 6 thumbnail dùng blob=09613399… = sheet.png của step, thấy lưới sprite thật (17_bundle_menu). API: blob rác → 400 invalid_hash, hash lạ → 404, hợp lệ → 200 static/animated.
5. WP-17D — ĐẠT: Audit Flutter có "Thiếu font" (đỏ), 2× "Font chưa khai báo" + nút "Khai báo font", "WAV nặng 2.00 MB" (chỉ báo, wav còn ở tab Không ai dùng); Xem trước → Ghi thêm đúng family Lato (italic) + Roboto (700) vào pubspec, tự quét lại còn 2 mục; Hoàn tác "Đã khôi phục 1 file", md5 cả cây khớp trước (18–21).
6. Console — ĐẠT: renderer.log 0× ResizeObserver, chỉ 3 WARN cố ý (blob rác 404/400, mục 2), 0 ERR, 0 pageerror ở mọi phiên CDP; core.log 0 phản hồi 5xx, 0 traceback.

### Lỗi / quan sát
| # | Mức | Mô tả | Vị trí |
|---|-----|-------|--------|
| 1 | nhẹ | Lightbox của cổng chọn là mã chết: `lightbox` không bao giờ được gán, nên phím S trong lightbox cổng chọn không dùng được | app/src/renderer/src/components/gate/GatePicker.vue:25,185 |
| 2 | quan sát | Godot/Unity: plan_id không giảm thời gian Ghi (plan chỉ sửa .import, 0,1 s); lợi ích chỉ ở Flutter (−24 s trên 349 MB) | core/studio_core/api/audit.py:109-135 |
| 3 | quan sát | Không có recipe CPU nào có gate.pick sẵn; muốn QA cổng chọn không GPU phải tự thêm recipe vào data/recipes | core/studio_core/recipes/*.yaml |
