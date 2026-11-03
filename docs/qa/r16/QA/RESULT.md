## WP-16QA — DONE (Electron thật + core thật, 2026-11-03, cửa sổ 1320x852, sáng, không GPU/ComfyUI)
Cách chạy: STUDIO_DATA_DIR=scratch/qa16-data, `electron-vite dev -- --remote-debugging-port=9361 --user-data-dir=…` + Playwright connectOverCDP. Seed: dự án "QA16 Game", 5 ảnh nhập thư viện (icon256 256px, icon1024 1024px, extra0-2), 2 run ui-juice chờ cổng, project Flutter mẫu 80 PNG 4,2 MB (335 MB). Đã xoá scratch/qa16-*.

### Kết quả từng mục
1. af524d7 — ĐẠT cả 5.
   - Gửi tới → Thư viện (RunDetail run nhập) → #/gallery lọc project+run, tile được chọn + nhấn mạnh (06). Cuộn chưa kiểm vì chỉ có 1 tile.
   - Ctrl+K "Chạy lại lượt gần nhất" khi 2 run ui-juice đang chờ cổng: chọn run đã xong (extra2), mở /runs/<id>, không toast 409 (07..09).
   - Audit Flutter 160 mục: Ghi → Huỷ khi còn chuẩn bị → thanh biến mất 0,33 s, toast "Đã dừng: ghi 0 file, 160 file chưa sửa" (đúng; r15 báo 0), hash cây không đổi; Huỷ giữa lúc ghi (3 file) → Hoàn tác "Đã khôi phục 3 file", md5 khớp (13..18).
   - Xem trước 160 mục: thanh "Đang lập kế hoạch · N/160 tệp" (53→159, ~37 s) + nút Huỷ từ 0,3 s; Huỷ trong lúc xem trước → toast "Đã dừng lập kế hoạch sửa" sau 0,54 s (12, 13).
   - Thư viện đổi cỡ cửa sổ thật (Win32 MoveWindow, 2×8 cỡ + 20 bước kéo) khi có tile chọn → renderer.log 0× ResizeObserver (r15: 18×) (19, 20).
   - Quan sát: bấm "Ghi" sau khi xem trước xong vẫn lập kế hoạch lại 40–57 s trước khi ghi.
2. WP-16A — ĐẠT.
   - Gallery chọn icon256+icon1024 → Gửi tới → Đóng gói → Android+iOS (Flutter còn tick): android/res/drawable-mdpi..xxxhdpi/*.webp (64→256 và 256→1024 px, xxxhdpi = gốc), kotlin/AssetStudioAssets.kt:90-91 `R.drawable.icon256_icon256`, `icon1024_icon1024`; ios/AssetStudio.xcassets/icon256.imageset + icon1024.imageset (Contents.json hợp lệ, @1x/@2x/@3x, @3x = gốc), Sources/AssetStudioAssets.swift. Flutter skip hiện rõ trong dialog. (21..23)
   - Tên resource Android = <asset>_<asset> (nợ lặp tiền tố còn nguyên; iOS tên sạch).
   - Menu "Xuất cho engine" của step ảnh đơn liệt kê Android (Compose) + iOS (SwiftUI) (24). CHƯA KIỂM: bấm xuất thật từ menu (hộp chọn thư mục native).
3. WP-16B — ĐẠT.
   - PackDialog: 6 preset đều có thumbnail 96 px; nghỉ = `preview?still=1` (static), hover → bỏ still (animated 2,5–50 KB webp), rời chuột → tĩnh, chip khác không đổi; focus bàn phím (Tab) vào chip → động (25..27).
   - BundleMenu (RunDetail sprite sheet sau Duyệt): cùng hành vi (29, 30). Step ảnh đơn không có danh sách shader.
4. WP-16C — ĐẠT, 2 lỗi nhẹ.
   - Flow editor: tạo "QA16 Juice nút" (đầu vào Ảnh + node ui-juice), kéo nối, Lưu (31..36). Gallery chọn 1 ảnh → Gửi tới → "Chạy flow: QA16 Juice nút" → form có ảnh + dự án điền sẵn (37, 38) → chạy, duyệt cổng, Xong 1/1 (39..41) → node đầu ra "Gửi tới…": Restyle (ref = sprite sheet, dự án đúng), Thư viện (lọc theo run), Back/Forward không lặp prefill (42..44).
   - Ctrl+K có mục "Chạy flow QA16 Juice nút" (nhóm Chạy chuỗi tự động); Enter → /flows/qa16-juice-nut/run (45, 46).
   - LỖI nhẹ: gõ đúng nhãn "Chạy flow QA16" hoặc "Chạy flow Gói UI" → 0 kết quả — core: CommandPalette.vue:129 so khớp `title` hoặc `chạy flow workflow ${title}` (xen "workflow"), không khớp nhãn hiển thị `Chạy flow ${title}` (dòng 131).
   - LỖI nhẹ: hash rác ?input.input1=deadbeef → toast "Không nạp được tệp gửi sang: invalid blob hash …" (phần chi tiết EN) (47).
5. Console — ĐẠT: renderer.log 0 WARN/ERR, 0× ResizeObserver, 0 pageerror; core.log 0× 5xx, 1 ConnectionResetError asyncio vô hại.

### Lỗi
| # | Mức | Mô tả | Vị trí |
|---|-----|-------|--------|
| 1 | nhẹ | Ctrl+K không tìm được flow bằng đúng nhãn hiển thị "Chạy flow <tên>" | app/src/renderer/src/components/CommandPalette.vue:129 |
| 2 | nhẹ | toast hash rác lẫn tiếng Anh | views/FlowStartView.vue (xử lý prefill input.*) |
| 3 | quan sát | Ghi sau xem trước vẫn lập kế hoạch lại (40–57 s trên 335 MB) | AuditView.vue apply() / core api/audit.py |
| 4 | nợ | tên resource Android <asset>_<asset> | core/studio_core/store/pack.py |
