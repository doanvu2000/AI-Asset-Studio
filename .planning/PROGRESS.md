# PROGRESS — nhật ký làm việc (resume sau crash)

Session mới: đọc file này TRƯỚC, rồi `git log --oneline -10` + `git status`. Cập nhật mỗi khi xong 1 bước / commit.
Lộ trình đầy đủ: `PLAN-architecture-v2.md` §5. Chủ nhân cho phép commit dần (không push, không trailer Co-Authored-By).

## Trạng thái phase

- ✅ P0 spike · ✅ P1 core+app (R1) · ✅ P2 (GPU affinity, R2, R3 Qwen) · ✅ UI art Codex — đã commit tới `f4323ef`.
- 🔄 P3 3D: R4 `prop-3d`, R5 `sprite-8dir-3d` (xem dưới).
- ⏭️ P4 Claude (brief/prompt expansion, R7 procedural-3d, Blender MCP step) · P5 Spine (R6) · P6 flow editor · P7 đóng gói.

## P3 — chi tiết

Code (agent P3 cũ, chết khi session crash 2026-10-18 12:49): `adapters/blender/{worker,executor,cleanup_math}.py`
(kind `blender.cleanup`), `ops/glb.py` (`op.glb_validate`), `op.sheet` pivot bottom, recipes `prop-3d.yaml`,
`sprite-8dir-3d.yaml`, sửa `sprite-8dir-glb.yaml`, `tests/test_p3_3d.py`, `scripts/e2e_r5.py`. pytest: 129 pass / 4 skip.

Live run (data dir trong scratchpad session `5661b5e9…`):
- R5a: mesh fail `comfy_crashed rc=3221225477` (access violation). R5b: mesh fail `RemeshMesh: CUDA error: unknown error`.
  R5b resume: OK — sheet 2048×256, 8 hướng khớp, chân thẳng, nền trong suốt → đạt.
- R4a: đang chạy (start 12:39), log có `cudaMallocAsync recovered from an allocation failure` → VRAM sát trần.

## Việc tiếp (theo thứ tự)

1. [x] R4a (code cũ): mesh `comfy_timeout` 1800s — VRAM sát trần (Z-Image còn nạp) → remesh/QEM cực chậm.
2. [x] Review + commit P3 (`d1655d8`).
3. [x] Ổn định TRELLIS (`ad51647`): CUDA error → retryable; engine auto-retry (`auto_retry`, Comfy=1, `/free` giữa 2 lần);
   `_free_before: true` ở bước mesh R4/R5. pytest 132 pass.
4. [x] R4b live OK 356s (concept 51s, TRELLIS 259s @1024/512, cleanup 1.6s, turntable 2.5s), GLB 29k tris 3 tex — đẹp.
   Thêm `_timeout_s: 900` cho bước TRELLIS (`d732799`).
5. [x] P4 Claude (`490ddff`): `${steps.x.out.field}` refs, claude.brief, blender.script, R7 procedural-3d
   (live 228s/$0.19, rương gỗ — đạt blockout), blender.agent (chỉ probe read-only live). pytest 153 pass.
   Mở: reviewer R7 khắt khe → thường chạy đủ 3 vòng; procedural_3d không giữ lane BLENDER.
6. [x] P4b MCP (`aac51e2`): `python -m studio_core mcp`, lock `<data>/core.lock.json`, E2E claude -p + codex exec (CPU recipe).
   Codex cần `tool_timeout_sec=900`, `default_tools_approval_mode="approve"`. Chưa E2E GPU qua MCP (R1/R5) → lead làm.
7. [x] 1 core / data dir (`7c0d955`): spawn lock, serve thoát mã 3 nếu đã có core, app + MCP attach qua lock. pytest 159.
   Mở: `npm run typecheck` (web) có lỗi sẵn ở ws.ts, StatusGlyph.vue, NewRunView.vue, TS6307 preload/types.ts.
8. [x] P6 (`eeac72c`): Vue Flow editor, /api/kinds|recipes validate/save|batches|presets, batch UI, preset Unity/Godot.
   pytest 184, web typecheck sạch, chưa chạy với core thật (chỉ mock + pytest). Quy ước hướng 0=S,45=SW,90=W đã đối
   chiếu sheet R5 thật (frame 90° quay trái) → đúng. Mở: recipe user lưu mất comment YAML; batch list chỉ 500 run gần nhất.
9. [x] R5 live lại @1024/512: 192s (TRELLIS 119s). Mặt trước tốt; lưng có texture "mặt ma" (TRELLIS bịa mặt sau từ 1 ảnh)
   → cải tiến sau: thêm ảnh mặt sau (Qwen) làm input đa góc nếu workflow hỗ trợ.
10. [x] E2E GPU qua MCP: `claude -p` (sonnet) chạy R1 auto_first → final.png 66.5s; core MCP tự tắt, lock xoá.
11. [x] P5 R6 spine-character (`35018e8`): SAM3.1 seed → partition → Claude joints → layers → Flux2 fill → Spine 4.2.
   Live 2 run ~108s, setup pose IoU 0.9994, anim relax ổn. HTML spine-player 4.2.43 đã mở bằng Chrome headless → runtime load + render đúng (lead kiểm 15:37).
   Mở:
   JSON chưa thử trong Spine Editor; chỉ front-view T/A-pose; Claude joints sửa rất ít.
   Workflow mới `segment_sam3`, `inpaint_flux2_klein4b` nằm ở L:\AI\setup\workflows (ngoài repo, có index.json.bak-p5).
12. [ ] R3 live qua MCP OK 177s (Qwen 134s); góc 45/90/135 gần như cùng profile → đúng hạn chế đã biết, R5 tốt hơn.
13. [~] Agent: app viewer GLB (three.js) / Spine (spine-player) / sprite sheet + catalog kinds cho op.parts/layers/spine_export.
14. [ ] P7 đóng gói — sau 13 (cùng package.json). Rule: không tự build → agent chỉ chuẩn bị config/script, chủ nhân build.

## Nhật ký

- 2026-10-18 13:10 — resume sau crash; tạo file này; pytest xanh.
- 13:25 — commit P3 `d1655d8`, fix TRELLIS `ad51647`.
- 13:40 — NGUYÊN NHÂN CRASH: TRELLIS upsample 1536 → mesh thô 46M mặt → ComfyUI giữ ~25GB commit, pagefile cạn
  (Win32 1455, bash fork fail). Fix `3255a39`: `94.target_resolution: 1024`, `241.resolution: 512` ở R4/R5.
- Chạy song song 2 agent: P4b MCP (`core/studio_core/mcp/**`, main/api/app/contracts, app nút copy lệnh) và
  P4 Claude (brief expansion, `blender.script`, R7 procedural-3d, `blender.agent`). Agent chết → xem file đã đổi
  bằng `git status`, giao lại phần dở. Chỉ lead chạy recipe GPU.
- 15:21 — 2 agent P6 + P5 bị rate limit (14:xx), quota reset 15:20 → đã resume bằng SendMessage. File dở nằm trong working tree (chưa commit).
- 15:55 — Agent viewer (GLB/Spine/sheet, app) + agent R5 multi-view (Pixal3DMultiViewConditioning, Qwen sinh trái/sau/phải;
  giữ GPU) đang chạy. Lead không chạy GPU tới khi agent MV báo xong.
