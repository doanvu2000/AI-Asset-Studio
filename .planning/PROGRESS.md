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

1. [ ] Chờ R4a xong → kiểm GLB + turntable.
2. [ ] Review code P3 → commit P3.
3. [ ] Ổn định bước mesh TRELLIS: `/free` ComfyUI trước TRELLIS, auto-retry lỗi `retryable` (comfy_crashed / CUDA unknown).
4. [ ] Bắt đầu P4.

## Nhật ký

- 2026-10-18 13:10 — resume sau crash; tạo file này; pytest xanh.
