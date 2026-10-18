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
4. [ ] Dừng run R4a cũ (PID 37548 script, 38444 core, 38356 comfy — bị chặn kill tự động, cần chủ nhân) → chạy lại R4 bằng
   `core/scripts/e2e_r5.py` (recipe prop-3d) với code mới, kiểm GLB + turntable.
5. [ ] Bắt đầu P4.
6. [ ] P4b — MCP server cho Claude CLI / Codex CLI (thêm vào plan §3.6 + §5 lúc 13:35).

## Nhật ký

- 2026-10-18 13:10 — resume sau crash; tạo file này; pytest xanh.
- 13:25 — commit P3 `d1655d8`, fix TRELLIS `ad51647`.
