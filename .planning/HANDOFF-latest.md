# HANDOFF — AI Asset Studio (2026-11-03, sau lượt 13)

Trả lời tiếng Việt, gọi "chủ nhân", xưng "con". Nhật ký: `.planning/PROGRESS.md` (phiên lượt 12). Plan: `.planning/PLAN-2026-11-03-r12.md`. Brief/báo cáo từng gói: `.planning/wp/WP-12*.md`.

## 1. Đang ở đâu
- Lượt 12 (manager-work-agent) XONG, không còn agent chạy. Commit từng phần + push cuối đợt (DECISIONS.md 2026-11-03). Full pytest 1749 pass / 6 skip (song song xdist loadfile, 74 s); typecheck sạch.
- 3 tính năng mới: F theme UI từ style bible (c10ed32), D normal map AI MoGe-2 CPU (1fa8db5 + pack cài từ app 202149e), G Compose (3fcbb70) + SwiftUI (d11b331) cho 7 kind.
- Tối ưu: versions phân trang (bf2c73a), pytest-xdist + slow (4d51c8c), lut._solve 3.5–4x (f192457), bỏ motion-v index 695→327 kB (2f0562e), tự xuất khi duyệt (ea5b588), chạy lại + seed (56a6ca2, e25f458), Gallery lọc 1 hàng + Flow undo (b882dbe), preset/biến thể seed/auto-pick QA (0aad2e2), MCP (8d37f65), QA Electron thật (7ce0367), fix QA (a9f6746), README + changelog 1.5.0-dev (22a69dd).

- Lượt 13 (PLAN-2026-11-03-r13.md) XONG: normal AI worker ấm 1.7 s/ảnh (3e13f84), tiến độ + huỷ quét Audit/Restyle/QA (43262b9), menu Chạy lại ở Lịch sử + Delete dây + bàn phím node (e2bf0ee), QA thật docs/qa/r13/QA, fix QA_VERSION renderer (2f04cd0), thanh quét về đủ (11193e2). Full pytest 1770 pass.

## 2. Việc tiếp / nợ
- Chủ nhân xoá tay `L:\AI\setup\_ni test` (~1 GB, thư mục test cài normal-ai).
- Chưa kiểm: auto-pick QA chạy thật (cần GPU); SwiftUI chưa biên dịch (cần Mac: `FXI_OUT=<dir> pytest tests/test_export_ios_fx.py` rồi `swiftc -typecheck`); Compose/SwiftUI chưa chạy thiết bị; nút cài normal-ai trên UI; confirm xoá node có dây; 
- test_r8_style chỉ nhanh 1.5x (CG cần multigrid). Normal AI lạnh 4.2–5.8 s/ảnh.
- Step map không pin seed; preset chưa đổi tên được; mũi tên giữa node nhảy sang node xa khi hướng đó trống; audit/fix và project_qa chưa có tiến độ.
- Voice: vẫn chờ chủ nhân chọn giọng (L:\AI\tts-lab\voice-compare.html) → PLAN-2026-11-03-voice.md V1–V4.
- Pin seed mặc định khi chạy lại: vẫn KHÔNG (chủ nhân chưa quyết; chỉ có nút "giữ seed" thủ công).

## 3. Ràng buộc
- Flutter/Flame đích mặc định; Android Compose + iOS SwiftUI. Không LoRA, không cloud, không Rust. SFX/music dừng.
- GPU dùng chung gen-pose-line — không tự khởi ComfyUI. Tối đa 3 agent viết code song song.
- Tắt tiến trình theo PID, không taskkill /IM. Không force push / reset --hard.
- Hook block-no-verify bắt nhầm cờ `-n` của pytest nếu cùng lệnh với `git commit` → tách lệnh.
- Trước khi commit file nhiều agent cùng sửa (runner.py, mock/server.ts, types.ts) xem diff — tránh cuốn code dở của WP khác.
- Đổi recipe/MCP → `cd core && .venv/Scripts/python.exe ../scripts/gen_agent_docs.py`.
- QA Electron thật: nhật ký `.planning/wp/WP-12QA.md` + `docs/qa/r12/QA/RESULT.md`.
