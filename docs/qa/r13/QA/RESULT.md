## WP-13QA — DONE (Electron thật + core thật, 2026-11-03, cửa sổ 1320x852, sáng, không GPU)
Cách chạy: STUDIO_DATA_DIR=L:\AI\AI-Asset-Studio\scratch\qa13-data, `electron-vite dev -- --remote-debugging-port=9334` + Playwright connectOverCDP.

### Kết quả từng mục
1. Lịch sử ⋯ chạy lại — ĐẠT: run ui-juice (seed 7) hoàn tất có nút ⋯ (menu "seed mới / giữ seed", cả 2 bật); giữ seed và seed mới đều ra run mới, step "Dùng lại" (seed tường minh 7 nên cache hit, như r12); Esc đóng + trả focus. ⋯ không hiện khi run Chờ duyệt (đúng thiết kế). (07..10)
2. Flow editor — ĐẠT: kéo nối dây thật, bấm dây = chọn (thanh "Gỡ dây"), Esc bỏ chọn, Delete gỡ, Ctrl+Z/Ctrl+Y đúng; Tab vào node, mũi tên chuyển node, Enter mở panel. Quan sát: không có node trong hướng thì nhảy sang node xa (Phải từ node trên sang node dưới). (13..18)
3. Quét + Huỷ — ĐẠT phần bar/Huỷ, 1 LỖI nhãn:
   - Audit 4000 PNG (project tạm, chỉ đọc): "Đã quét 601/4.001 tệp" + thanh, Huỷ → toast "Đã huỷ quét". Unity thật quét xong 409 tệp 3,75 s. (19..24)
   - Restyle 4000 ảnh: "Đã quét 340/4.000 ảnh" → Huỷ → toast "Đã huỷ quét". (25,26)
   - Gallery chấm lại QA (730 version tạm trong DB tạm): "Đã quét N/500 ảnh" rồi 0/172; Huỷ ở 293/500 → toast "Đã dừng chấm điểm, 301 ảnh đã có điểm". (27..30)
4. normal AI — ĐẠT: 3 ảnh 256 px liên tiếp method=AI: 4,0 s / 1,7 s / 1,6 s (lạnh → ấm); 1 worker dùng chung; tự tắt ~120 s sau lần cuối (PID 1848, 31516 chết); spawn lại lạnh 9,5 s; đóng app (CDP Browser.close) → Electron, core và worker (PID 34972, 33164) đều chết, không còn python normal_ai_worker. (31,32)
5. Console — xem dưới.

### Lỗi
- L1 (thấp-vừa, tái hiện 2/2): nhãn Gallery "Chấm điểm N ảnh cũ" không bao giờ giảm, kể cả sau chấm xong hoặc reload. Bước: Gallery có ảnh (qa NULL) → bấm nút → chờ xong → nhãn vẫn "728 ảnh cũ". Nguyên nhân: renderer QA_VERSION = 1 (app/src/renderer/src/composables/qa.ts:7) lệch core QA_VERSION = 2 (core/studio_core/engine/qa.py:20), nên mọi ảnh đã chấm bị tính chưa chấm (GalleryView.vue:203-208).
- Quan sát: thanh Audit dừng ở ~318/409 khi sang phase "Đang phân tích…" (không về 409/409); huỷ trả HTTP 409 nên console có "Failed to load resource" (không phải lỗi UI).

### Console
renderer.log: 3× ResizeObserver (vô hại). Trình duyệt: 2× "Failed to load resource 409" (Huỷ audit/restyle). Không pageerror nào. core.log: chỉ ConnectionResetError asyncio khi reload (vô hại).

### Ảnh
35 ảnh docs/qa/r13/QA/*.png (01..32, 1320x852). Dọn: tắt theo PID, xoá scratch/qa13-*.
