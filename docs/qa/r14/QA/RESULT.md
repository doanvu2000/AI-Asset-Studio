## WP-15QA — DONE (Electron thật + core thật, 2026-11-03, cửa sổ 1320x852, sáng, không GPU/ComfyUI)
Cách chạy: STUDIO_DATA_DIR=scratch/qa15-data, `electron-vite dev -- --remote-debugging-port=9335` + Playwright connectOverCDP; đổi cỡ cửa sổ bằng Win32 MoveWindow lên chính cửa sổ Electron. Lưu ý: cây nguồn đang được WP khác sửa song song (AuditView.vue, audit.py...), kết quả ứng với bản đang chạy lúc QA.

### Kết quả từng mục
1. NewRun preset — ĐẠT, 1 LỖI nhỏ. ui-juice, chế độ "1 prompt × nhiều seed" (5 lượt, seed gốc 20): lưu "Alpha" → đổi tên "Beta" → ghi đè (2 bước, toast "Đã ghi đè…") bằng giá trị mới (glow/sparkles 2/3 lượt/seed 77) → đổi hết field và về "Một prompt" → áp "Beta": khôi phục đủ field, bật lại chế độ seed, N=3/seed 77. Tên trùng: client chặn (nút Lưu tên disabled + "Đã có thiết lập trùng tên."). LỖI: khi list cũ (stale) và server trả 409 thật, toast là tiếng Anh thô "Không đổi được tên: a preset named 'Delta' already exists" (không dịch VN). (02..12)
2. Flow editor nhiều cột — ĐẠT. 6 node kéo thật vào 4 cột so le; 24 tổ hợp node × mũi tên: 24/24 khớp nón ±45° (14 chuyển node, 10 hướng trống đứng yên, vd ui_juice_2 → Phải không nhảy chéo xuống ui_juice_3); Tab tuần tự; Enter/Space mở panel đúng node. 0 lỗi console. (13..15)
3. Console ResizeObserver — ĐẠT. Đổi cỡ cửa sổ thật (960–1484 px, 2 lượt 7 cỡ + đợt kéo liên tục 15 bước) trên 12 trang có PageHeader: renderer.log 0× "ResizeObserver" (r13: 3×), console 0, window.error 0. (16)
4. Audit fix + Hoàn tác — ĐẠT. Project Godot tạm 2540 ảnh/.import (353 MB). Ghi → "Đang chuẩn bị…" → "Đã sửa 0/2.540 … 407/2.540 tệp"; Huỷ lúc chuẩn bị → toast "Đã dừng: ghi 0 file, 2540 file chưa sửa" (hash không đổi); Huỷ lúc 407 → toast "Đã dừng: ghi 494 file, 2046 file chưa sửa · hoàn tác được ở backup …"; Hoàn tác → "Đã khôi phục 494 file"; md5 toàn bộ .import trước = sau (7632275c…). (20..26)
   Quan sát: toast chỉ hiện sau ~10 s vì re-scan chạy trong cùng request fix; trong lúc đó thanh hiện "Đang chuẩn bị…" + Huỷ bật (trông như kẹt, không phải lỗi). Backup ghi "408 MB → 27.0 MB" tính cả phần không ghi (core/studio_core/export/audit.py ~946-948: gpu_before/after cộng theo `todo`, không theo `written`).
5. Project QA — ĐẠT. Dự án tạm 18 run × 8 ảnh (144 version, trần 120): thanh "Đã đo N/120 ảnh" + Huỷ → toast "Đã huỷ kiểm tra, giữ báo cáo cũ", thanh biến mất ngay, UI không treo, chạy lại đủ 7,5 s (khoá busy nhả), báo cáo cũ giữ nguyên khi huỷ. (30..36)

### Lỗi
- L1 (nhẹ): 409 preset trùng tên hiện tiếng Anh thô trong toast VN — core/studio_core/api/run_presets.py:91 (message EN) + app/src/renderer/src/components/RunPresetMenu.vue:119 (toasts.error nối message thô).
- Quan sát: toast huỷ audit fix trễ ~10 s; gpu_before/after backup huỷ dở không phản ánh phần đã ghi (nhẹ).

### Console
renderer.log: 0× ResizeObserver; WARN 409 preset (cố ý) + 2× 409 cancelled QA. Browser: "Failed to load resource 409" cho các lần huỷ/409 cố ý. Không pageerror.

### Ảnh
31 ảnh docs/qa/r14/QA/*.png (1320x852). Dọn: tắt theo PID (cmd 44328, node 3336/44412, electron 37724/44036/35284/33724, core python 44624/44636 + conhost), xoá scratch/qa15-* (qa15f-* là của WP khác, không đụng).
