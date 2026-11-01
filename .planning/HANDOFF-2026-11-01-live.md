# HANDOFF — AI Asset Studio lượt 8 live (2026-11-01)

Đọc file này là làm tiếp được. Trả lời tiếng Việt, gọi "chủ nhân", xưng "con". Thay `HANDOFF-2026-11-01-0517.md`.
Plan: `.planning/PLAN-2026-11-01-r8-live.md`. Nhật ký: `.planning/PROGRESS.md` (mục "Phiên 2026-11-01 — manager-work-agent (lượt 8 live)").

## 1. Đang ở đâu
- master, commit từng phần (chủ nhân cho trong đợt này), **chưa push**. Không agent nào chạy, không `.gpu-lock`.
- Full pytest **1320 pass / 5 skip**, app typecheck sạch, gen_agent_docs --check OK.

| Gói | Commit | Kết quả |
|---|---|---|
| L1 D7 spine-physics Unity thật | 8a432c9 | cape 4 xương run-stop 104°→65°, tắt 5.6→2.0s, walk 7.4°; op v4 |
| G1 D1 tile-animated | 58f621f | Wan live seam 0.3675; retry → CPU fallback, torch mask lửa; op v3 |
| G4 D4 decal crack | 1098e94 | std bề rộng 1.3→3.2, nhánh cong cùng màu; DecalPack v2 |
| L4 app xem bằng mắt + peek/quick view | e05a4c5 | BundleMenu Toon tràn đáy (lỗi thật) đã sửa; RecipePeek/RecipeQuickView |
| L5 demo + chữ cụt | a4265d6 (core) · ad75915 (app) | 9/90 recipe có demo thật; /runs?recipe_id; /recipes/{id}/preview; VN allow_fail |
| G3 D3 spine-face | 123df97 | mẩu mày rời 2→0; live mũ/che mày/nghiêng; plan/build v3; test cloth cũ chỉnh theo D7 |
| L6 QuickView sơ đồ dài | ab64326 | xếp dải ≤5 cột + auto-fit |
| G5 D5 ui-kit vùng chữ | 6f9a091 | brass std 15.4→4.1 (mask cosine feather), prompt mới sạch; NineSlice v2 |
| L2 engine (#59/#60/#63/#64/#65) | — | Godot 4.7.2 + Unity OK, #60 girl lệch 0px; không đổi code |
| L3 Blender (#61/#64) | — | LOD1 50% tri, IoU ≥0.95, hull ≤1.4; toon band OK; không đổi code |

## 2. Việc tiếp
1. **Chờ chủ nhân chọn**: cloth 2 xương walk chỉ ~2.6° (spine-core) / 0.3–0.5° (Unity girl) sau D7 — muốn sống hơn thì chỉnh riêng chuỗi 2 xương, đổi lại run-stop cao hơn.
2. Outline toon3d chưa nhìn bằng mắt với GLB thật trong Unity/Godot (L2 chỉ compile shader, mock GLB).
3. QuickView: chữ node nhỏ ở zoom 0.4, dây nối giữa các dải chạy vòng hơi rối.
4. 81/90 recipe chưa có demo (chưa từng chạy) — chạy thử rồi thêm `preview:` + ảnh vào `core/studio_core/recipes/previews/`.
5. Bump APP_VERSION 1.3.0 khi phát hành; push khi chủ nhân bảo.

## 3. Nợ
- spine-face: mày dưới mũ Klein vẽ nhạt hơn mày gốc; profile miệng/mày có thể ra mép mặt (chưa ràng buộc).
- ui-kit-style chưa chạy live (dùng chung NineSlice + câu prompt).
- #60: mesh Unity mất weights (chưa SpriteSkin), slot color timeline bỏ; bake dùng `Pose._b` private.
- Core export step chưa có cờ mesh (app đoán GLB theo output/kind).

## 4. Quyết định và ràng buộc
- Không audio/TTS, không train LoRA, không cloud, không Rust. Unity 6000.3.27f1, Godot 4.7.2 (`L:/AI/tools/godot`).
- Script/shader dùng chung giữ GUID cố định. Đừng đề xuất tách namespace.
- Git: push chỉ khi chủ nhân bảo, không trailer. Build app chỉ khi bảo. Đợt mới phải hỏi lại cách commit.
- GPU: `mkdir .gpu-lock` + owner, chỉ xoá khoá mình. Project test Unity/Godot chỉ dùng bản copy (L:\AI\r8-*, đã xoá).
- Subagent: dặn tắt mọi background task trước khi báo cáo (đợt này 3 agent treo nền phải TaskStop).
