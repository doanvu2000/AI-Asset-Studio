# PLAN — Spine weighted mesh cho vai (2026-10-27)

Vấn đề: lớp `upper_arm_*` là ảnh cứng xoay quanh khớp vai → phần nằm phía trong khớp (khăn lông barbarian, mép giáp vai)
dựng đứng lên khi hạ tay.

## Cách làm

1. `spine.py` `arm_mesh(layer, bones)`: lưới đều (ô ~ min(w,h)/6, 8–24 px) phủ cả ảnh lớp; đỉnh viền xếp trước theo chu vi
   (`hull`), rồi đỉnh trong. `uvs` 0..1, `triangles` 2/ô.
2. Trọng số mỗi đỉnh theo t = chiếu lên trục vai→khuỷu / dài upper_arm: w_arm = smoothstep(T0 −0.05 → T1 0.35);
   phần còn lại cho bone `torso`. Đỉnh ghi theo JSON Spine weighted: `[n, boneIdx, bx, by, w, …]` (bx, by = toạ độ
   setup trong không gian bone).
3. `build_skeleton`: slot `upper_arm_*` dùng attachment `type: mesh` (cùng region atlas). Lớp khác giữ region.
4. `compose`: vẽ mesh — biến dạng đỉnh theo world transform từng bone, raster tam giác bằng barycentric (numpy), lấy
   mẫu bilinear ảnh nguồn (premultiplied). Setup pose phải trùng ảnh gốc (iou ≈ 1).
5. `validate_skeleton`: mesh cần region atlas; số uvs = 2×số đỉnh; chỉ số tam giác hợp lệ; bone index hợp lệ; tổng w ≈ 1.
6. Test: mesh setup == region setup; vertex đỉnh phía trong vai không đổi vị trí khi upper_arm xoay; validate bắt lỗi.
7. Kiểm: dựng lại offline barbarian / knight / girl / samurai / pirate; build Portable; lượt thật; xem spine-player (html).

## Ngoài phạm vi (để sau nếu cần)

- Mesh cho thigh/hip (váy dài), cape theo torso.
