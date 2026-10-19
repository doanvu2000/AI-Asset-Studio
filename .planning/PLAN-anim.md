# PLAN — Animation (2026-10-19)

Chủ nhân chọn cả 3, cho tự chạy GPU. Thứ tự rẻ trước.

## A. Spine thêm anim (CPU) — `core/studio_core/ops/spine.py`
- `animations()` nhận bones (world rot) + chiều cao + phía trái/phải tính từ khớp (không đoán dấu).
- Thêm `walk` (vòng 1s: hông nhún, chân nhấc luân phiên, tay hạ + đung đưa), `attack` (tay phải vung: lấy đà → chém
  → về), `hurt` (giật người + nháy đỏ slot rgba), `jump` (nhún → bật → đáp). Giữ `idle`, `relax`.
- `anim_frames.png` thêm khung walk/attack. Test: anim hợp lệ (validate), walk nhấc chân, attack di chuyển tay phải.

## C. Sprite 8 hướng có anim (GPU + Blender) — R5-mv thêm nhánh
- Bước `joints`: `claude.task` chấm 19 khớp trên khung mặt trước (render dir 0) — dùng lại schema bones của R6.
- Bước mới `blender.animate_dirs`: px → world (ortho_scale, pivot của render meta; độ sâu = tâm mesh tại độ cao đó),
  dựng armature theo khớp, parent mesh automatic weights (lỗi → envelope), action walk N khung (procedural), render N
  khung × 8 hướng cùng camera với render_dirs. Ra ảnh theo hướng-major + meta.
- `op.sheet` hàng = hướng, cột = khung (viewer sprite đã hỗ trợ hàng = hướng). Input recipe `anim: none|walk`.

## B. Spine nhiều góc (GPU)
- Recipe mới `spine-character-mv`: sau `pick` thêm Qwen mặt sau (workflow multi_angles_qwen2511, prompt 180°), chạy
  nhánh rmbg → segment → parts → bones → layers → fill → export thứ 2 → ra 2 bộ Spine (front/back).

## Kiểm
- pytest + typecheck mỗi phase; live GPU: C trên R5-mv, B trên recipe mới; xem viewer qua app dev + CDP.
