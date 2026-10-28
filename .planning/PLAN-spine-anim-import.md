# PLAN — Thêm anim cho skeleton Spine có sẵn (yêu cầu từ session spine-test)

Nguồn: `L:\AI\project\spine-test\asset-studio\REQUEST-spine-anim-import.md` (R1–R5).

## Quyết định
- Render strip bằng **runtime Python riêng** (`ops/spine_runtime.py`): atlas parser (bounds/offsets/rotate), bone
  pose + IK 2 bone (stretch, bendPositive), timeline bezier 4.2, skin ghép, region + mesh (weighted/không), draw order.
  Không cần Chrome/CDN, chạy được trong core frozen. Đối chiếu với `@esotericsoftware/spine-core` (node, chỉ lúc dev).
- Input `file` (ext filter, `siblings: atlas_pages` → MCP/app tự gom các trang png theo atlas, giá trị là list blob,
  tên giữ trong blob name) và `video` (mp4/webm/gif). Cả hai vào BLOB_TYPES.
- ffmpeg: setting mới `ffmpeg_exe` (dò PATH/winget), dùng cho `op.video_frames`.
- Op mới: `op.video_frames`, `op.spine_rig_summary`, `op.spine_add_anim` (port gen_sway), `op.spine_render_strip`;
  `claude.spine_review_loop` (adapter: render → ask() chấm → add_anim → lặp, giữ lịch sử critique, Codex tuỳ chọn).
- Recipe `spine-anim-ref` thay recipe tạm `spine-motion-spec` (giữ tạm file đó trong Portable, không ship).
- R5: output `json` có `score` → badge điểm ở gate/run list; modal "Xem kết quả chấm" (critique, strip vs ref, diff
  spec, tải JSON, lịch sử vòng). MCP `pending_gates` kèm score/critique.

## Bước
1. Core: input types + blob mime video + MCP gom sibling.
2. `spine_runtime.py` + test đối chiếu spine-core.
3. Op video/rig/add_anim/render_strip + test.
4. `claude.spine_review_loop` + recipe + test (fake ask).
5. App: picker file/video, badge + modal chấm điểm.
6. Chạy R4 qua MCP (core dev), báo spine-test.
