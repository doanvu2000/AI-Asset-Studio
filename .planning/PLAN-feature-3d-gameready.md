# PLAN — #1 3D game-ready (retopo / LOD / bake)

Mục tiêu: mesh TRELLIS (đã `blender.cleanup`) → bộ GLB dùng thẳng trong game: LOD0..N theo ngân sách tam giác,
normal map + AO bake từ mesh gốc dày, UV gọn.

## Hiện có
- `blender.cleanup` (worker.py `cleanup`): import, merge, decimate COLLAPSE tới `target_faces`, smooth by angle, scale,
  origin, export GLB. Không bake, không LOD, không remesh.
- Worker Blender thường trú, RPC JSON-lines, một call/lần; test dùng FakeBlender, live test đánh dấu `live`.

## Thiết kế
Handler worker mới `game_ready` + executor `blender.game_ready` (Lane.BLENDER, cacheable theo worker_hash).

Params: `mode` decimate | quad, `target_faces` (LOD0, mặc định 5000), `lods` (mặc định [1, 0.5, 0.25] tỉ lệ so
LOD0), `bake` (normal, ao), `texture_size` 1024, `smooth_angle`.

Luồng trong Blender:
1. Import GLB gốc (high) giữ nguyên làm nguồn bake.
2. LOD0:
   - `decimate`: bản sao high, COLLAPSE tới target_faces (giữ UV + texture gốc).
   - `quad`: QuadriFlow tới target_faces (mất UV) → Smart UV Project → bake base color từ high (selected-to-active,
     cage extrusion theo kích thước bbox) thành texture mới.
3. Bake normal (tangent) + AO từ high → LOD0 bằng Cycles (GPU nếu có, mẫu thấp), gắn vào material (normal map node,
   AO qua glTF Material Output "Occlusion").
4. LOD1..: decimate từ LOD0 (dùng chung UV/texture LOD0).
5. Export mỗi LOD một GLB (`<stem>_lod0.glb`…) + PNG normal/AO; report: faces mỗi LOD, thời gian bake, lệch
   bbox giữa LOD.

Recipe: `prop-3d` thêm bước tuỳ chọn `gameready` sau `cleanup` (input `gameready`: off | decimate | quad), validate
LOD0, turntable vẫn từ bản cleanup hoặc LOD0. Xuất engine: bundle gồm các LOD (Godot tự sinh LOD → chỉ LOD0 +
texture; Unity: các GLB LOD + ghi chú LODGroup) — bước sau.

## Kiểm
- Unit: params normalize, executor với FakeBlender (outputs, tên file, meta).
- Live (`-m live`, Blender thật): 1 GLB TRELLIS có sẵn trong data → faces đúng ngân sách ±10%, normal map không
  rỗng, ảnh turntable high vs LOD0 gần giống.

## Rủi ro
- QuadriFlow chậm/ lỗi trên mesh không manifold → fallback decimate + ghi report.
- Bake Cycles chậm trên CPU → mẫu thấp (16), texture 1024 mặc định.
