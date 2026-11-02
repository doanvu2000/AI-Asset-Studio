# PROGRESS — AI Asset Studio

Chỉ giữ phiên đang chạy (≤ ~150 dòng). Phiên cũ đã đóng → `progress/PROGRESS-<ngày>.md`.
Resume: đọc HANDOFF mới nhất trước; chỉ mở file lưu trữ khi cần tra lịch sử (grep theo mã WP/commit).

## Lưu trữ
| File | Nội dung |
|---|---|
| progress/PROGRESS-2026-10-early.md | trạng thái phase P1–P3, việc còn mở ban đầu |
| progress/PROGRESS-2026-10-27.md | phiên 27/10 |
| progress/PROGRESS-2026-10-28.md | phiên 28/10, R6–R11, G1–G2, F1–F6 (Spine, 3D, VFX, LoRA spike) |
| progress/PROGRESS-2026-10-29.md | phiên 29/10, lượt 5–6 |
| progress/PROGRESS-2026-10-30.md | phiên 30/10, lượt 7 |
| progress/PROGRESS-2026-10-31.md | phiên 31/10 |
| progress/PROGRESS-2026-11-01.md | lượt 8, live r8, lượt 9 (workflow) |

## Phiên hiện tại
## Phiên 2026-11-02 — manager-work-agent (lượt 10)
Commit từng phần, không push. Plan: PLAN-2026-11-02-r10.md.
- [~] WP-A RecipeInfo.outputs, WP-B sơ đồ, WP-C character-pack atlas+engine giao (sonnet, nền).
- [x] WP-B sơ đồ — FlowCanvas fitWidthZoom (RunDetail 0.4), minimap góc dưới trái ẩn khi canvas <640x420, dây nhảy hàng đi kênh giữa lane (graph.ts routeEdges dùng chung workflow); typecheck sạch; chưa chụp ảnh — 69d5d06
- [~] WP-D/E1 hàng đợi GPU (demo recipe + level-pack/ui-pack live) giao sonnet trong worktree HEAD, .gpu-lock.
- [x] WP-A RecipeInfo.outputs — contracts/recipe.info, app editor bỏ getRecipe; pytest l5 7 pass, typecheck sạch — 36cfba3. Ghi chú: test_w17_fx_qa fail KeyError fx_shadow do WP-C đang sửa kinds/ops — kiểm khi WP-C về.
- [x] WP-C character-pack gom atlas + xuất engine — op.engine_pack v2 (atlas MaxRects, zip Unity/Godot deterministic, clips.json), recipe character-engine-pack, node cuối character-pack, tag unity/godot; review sonnet 1 HIGH + 4 MED đã sửa; pytest 67 pass liên quan, full 1375/5 skip (agent), docs rc=0 — 95ca96a
- [~] 07:06 chủ nhân bảo nhả GPU ~5 phút cho gen-pose-line; đã nhắn agent GPU: huỷ job dở, tắt ComfyUI/core, xoá .gpu-lock, chờ ≥5 phút + nvidia-smi rảnh rồi chạy tiếp.
- Chủ nhân cho tự test + commit + push; giao đợt 1 r10b: F1 nhường GPU (opus), O1 issue có mã (sonnet), F3 thông báo + ETA (sonnet). Plan: PLAN-2026-11-02-r10b.md
- [x] F3 thông báo workflow-run (gộp ≥3/1.5s, bật/tắt ở Cài đặt) + chip ETA p50 recipe trên RunRowItem; core không đổi; typecheck sạch — 7c92927
- [x] O1 issue workflow có code+params (24 code), app dịch theo code (11 câu VN, còn lại fallback message); pytest flows 58 pass, full 1391/5 (agent); typecheck sạch — 3c5ea35
- [x] O2 restyle gỡ style cũ (strip_style khớp chính xác, $meta.style_base) — test style 17 pass, full 1400/5 (agent) — 6d31186. Còn: runner start_run ghi style_base mọi run (giao vòng sửa F1).
- [x] U1 UI/UX — grid minmax(0,1fr) chống tràn (base .stack, CostStats, RunDetail), Gallery tên 2 dòng, Projects stats đáy, ProjectDetail thu nhãn ≤1360px, NewRun clamp 3; ảnh trước/sau scratchpad 0bc24b1f…/u1/ (main xem RunDetail+Dashboard OK) — 9cb3e42 (DashboardView .sec commit cùng F1). Để lại: vạch ngăn thừa dải thiết bị, filter Gallery 3 hàng, chưa rà Flow*/RecipeEditor/Audit/Batch/Gates/Restyle, PageHeader thu gọn action.
- [x] F2 workflow hàng loạt — engine/flow_batch.py, bảng workflow_batches, 5 route /api/flow-batches, FlowStartView tab Hàng loạt, FlowBatchView, ISSUE_TEXT 24/24; pytest 58 pass (main), full 1407/5 (agent), typecheck sạch — 3c25b27 (kèm types/client GPU của F1).
- [x] F1 nhường GPU — engine/gpu.py GpuControl (hold lane gpu+blender, wait_idle, ComfyUI /free, tự hết hạn), scheduler ưu tiên float + chống đói 15 phút, /api/gpu*, GpuPanel Dashboard + store gpu + event gpu.updated; review opus 8 điểm đã sửa; runner ghi $meta.style_base (nợ O2); pytest 21 pass (main), full 1413/5 (agent), typecheck sạch — 3583768. Nợ: MCP chưa có tool GPU.
- [x] F1b MCP gpu_status/yield_gpu/resume_gpu/prioritize_run + SKILL docs; pytest mcp 15 pass (main), docs rc=0 — b54ef2d
- [x] U2 UI/UX đợt 2 — PageHeader tự compact (.lbl ẩn khi hẹp), grid minmax Settings/ProjectDetail/Audit/Restyle, Gallery filter 2 hàng + nút chấm điểm lên header, bỏ ellipsis → wrap; typecheck 0; ảnh scratchpad 0bc24b1f…/u2/ (main xem FlowStart 900 + Gallery 1100 OK) — 859e8bb. Còn: RunsView/NewRun/RunDetail/Recipes/Projects chưa áp .lbl.
- [x] D demo +11 recipe (22/89 có preview), main xem ui-kit + vfx-flipbook OK — a4fdf07. GPU nhả 07:12 → lấy lại 07:23 cho gen-pose-line.
- [x] E1 live: ui-pack OK 223–253s; level-pack fail `gather: no 8-direction character` → thêm input character optional map vào scene, chạy lại OK 236–249s; test 22 pass — 358f55b. Nợ: màu minimap lệch màn mẫu (nền xanh/đường nâu ngược); chưa thử truyền character tường minh.
- [~] E2 character-pack live GPU (atlas + xuất engine mới) giao sonnet.
- [x] E2 character-pack live 11/12 node (~23 phút), node style fail do chưa có version duyệt (chờ quyết định); engine pack 56 sprite, Godot import OK; fix README + minimap shadow layer; test 22 pass — 178df24. Kết đợt: HANDOFF-2026-11-02-r10.md
- [x] WP-S style audit bỏ qua + warning khi chưa có version duyệt (chủ nhân chọn); test 25 pass — 684a948
- Chủ nhân duyệt thứ tự tiếp: (1) anim scale + visor + style review:false, (2) kiểm app thật + mock lô, (3) Unity import engine pack, (4) .lbl 5 màn, (5) demo thêm.
- [~] WP-M minimap màu từ tileset/ảnh màn; WP-N1 (1); WP-N4 (4) giao sonnet. WP-N2 (2) chờ slot sau WP-M.
- [x] N4 .lbl header 5 màn (Runs, Recipes, Projects, Dashboard, RunDetail); typecheck sạch; chưa chụp (máy bận) — e26b208
- [x] N1 engine_pack v3 match_scale (thân anim khớp sprite hướng S, chặn >2x), style review:false; test 17 pass; chưa kiểm ảnh — 481debf. Visor: prompt video do claude.anim_motion trong ops/executors.py viết → sửa ở đó làm mất cache mọi op; để chủ nhân quyết.
- [~] N2b seed mock lô workflow/GPU/ETA giao sonnet (chỉ api/mock).
- [x] N2b seed mock 2 lô workflow + hàng đợi GPU 1 giữ/2 chờ; typecheck sạch (agent) — 1d4d474
- [x] WP-M minimap màu từ tileset (TMX/TMJ/LDtk) > ảnh reference > tên layer, op v2, level-pack map tiles+reference; ảnh compare scratchpad 0bc24b1f…/minimap/compare.png (main xem: nền be + đường xanh khớp màn); test 28 pass — aa1aebb. Nợ: tường/khối quantize gần đen, viền rào ngoài màn không lên minimap; mock app chưa map 2 input mới.
- [x] Minimap vòng 2–3: bỏ IntGrid collision đè tile + nền letterbox tối (461a2b4); ngã tư gộp cụm nhỏ ≤4 ô theo lân cận (chủ nhân chọn giữ bản mượt) — 3c62079; test 32 pass; ảnh compare4.png

## Phiên 2026-11-02 — manager-work-agent (lượt 11)
Chủ nhân: làm liền đợt 1 + 2 của PLAN-2026-11-02-r11-ideas.md; tự test + commit + push (như lượt 10). Máy có thể bận GPU → test có chọn lọc + typecheck, full pytest cuối đợt nếu máy rảnh.
Gói đợt 1: W1-A (U4 sidebar, U2 dự án mặc định + engine đích, U3 nút xuất engine, U8 dashboard bước tiếp) · W1-B (G2 style audit level/ui-pack, G4 thông báo theo lô + ETA workflow/lô, U7 then trong RecipeInfo, P3 FlowBatchView poll) · W1-C (P1 mime index, P2 preview cache, P3 poll GpuPanel/DeviceCard/FlowsView, P5) · W1-D (N4 khai báo AI, N6 bóng + viền) sau.
Đợt 2: U1 wizard, G1 game slice, N1 pixel cleanup, U6 gợi ý tiếp.
- 12:03 chủ nhân bảo tạm dừng ComfyUI cho gen-pose-line: không có ComfyUI nào đang chạy (GPU 100% là gen-pose-line app.py pid 58328); main giữ .gpu-lock để chặn agent dùng GPU.
- [~] W1-A xong (sidebar 6 mục + tab, engine_target, Dự án đầu tiên, ExportToEngine, GettingStarted động; test 17 pass, typecheck sạch) — chờ B/C xong vì chung contracts.py/types.ts/mock/db.py rồi commit.
- Chủ nhân: 99% dự án là Flutter (chính, cả Flame) + Android native, Unity/Godot chưa dùng → ưu tiên xuất Flutter/Android. Giao agent nghiên cứu hiện trạng export + thiết kế target flutter/android.
- [~] W1-B xong (style node level/ui-pack, batch_id + 1 thông báo/lô, ETA flow/lô, FlowBatchView event thay poll, RecipeInfo.then; test 77 pass + 258 -k; typecheck sạch) — chờ commit cùng A khi W1-C xong (chung contracts/types/db).
- Flutter/Android: plan PLAN-2026-11-02-r11-flutter.md; [~] FL-1 export/flutter.py + flutter_dart.py, [~] AN-1 export/android.py (file mới) giao sonnet. REG-1 + APP-1 sau.
- Chủ nhân: thêm iOS SwiftUI; Android = Jetpack Compose → nhắn AN-1 thêm Kotlin Compose helpers; IOS-1 giao khi có slot.
- [x] FL-1 export/flutter.py + flutter_dart.py (1x/2.0x/3.0x, ui_9slice.json + NineSliceImage/NineSliceComponent, sprite atlas+anim JSON + Flame helper, parallax, BitmapFont, spine, pubspec snippet, Assets.dart); test 9 pass; dart format OK, dart analyze chưa (cần project có flame) — 1b3a56c. Nợ: ui_9slice.json trùng khi gom nhiều asset (REG-1 xử lý); chưa tile_anim/autotile/vfx/cards.
- [x] Đợt 1 lượt 11 (W1-A sidebar/dự án/xuất engine, W1-B style lô/thông báo/ETA/then, W1-C mime/preview_hash/poll) — test 98 pass, typecheck sạch — 1dff58e (gộp 1 commit vì chung contracts/types/db). Còn: lọc step.log theo run (P5 events).
- [x] AN-1 export/android.py (drawable WebP mật độ, .9.png, mipmap adaptive, AssetStudioAssets.kt Compose: AssetStudioAnimation, ninePatchBackground); test 11 pass; Kotlin chưa compile — 66a2d3d
- Chủ nhân: iOS/Xcode tự test sau trên máy Mac → IOS-1 nghiệm thu bằng pytest + đọc cú pháp, ghi 'chưa compile Swift' vào handoff (việc chủ nhân).
- [x] IOS-1 export/ios.py + ios_swift.py (xcassets @1x-3x, resizing cap-insets, spriteatlas + AssetStudioAnimation TimelineView/SKTextureAtlas, parallax, AppIcon 1024); test 11 pass; Swift chưa compile (chủ nhân test Mac) — d424e58
- [x] G1 flows/game-slice.yaml (15 node, concept/sprite dùng chung) + N1 pixel-cleanup (dò pixel size, khoá palette, gỡ pixel lẻ); test 48 pass; palette từ style bible chưa (dùng palette_image/file)
- [x] FL-V Flame 1.38.2: dart analyze 1 error → 0 issue, flutter test 3/3 (NineSliceImage, loadAsAnimations, BitmapFont, NineSliceComponent, loadParallax); project tạm scratchpad 0bc24b1f…/flv/flv_app — 0f4b140
- [x] 12:23 Handoff mới: .planning/HANDOFF-2026-11-02-1223.md
- [x] W2-app GoalCards + NextStepChips — typecheck sạch, 979ffee (pushed). Chưa xem bằng mắt.
- [x] REG-1/APP-1 đích flutter/android/ios cho đóng gói + UI nhiều đích — 92 pytest pass (pack/export/projects), typecheck sạch. Còn: mcp/server.py + api/atlas.py vẫn unity/godot; BundleMenu chưa có ô webp/package.
- [x] REG-2 MCP build_asset_pack/export_to_engine + api/atlas + BundleMenu nhận flutter/android/ios — 38 pass, typecheck sạch. MCP default engines = flutter.
- [~] Giao 3 agent nền (CPU): N4 khai báo AI (store/ai_credits.py, AiCreditsDialog), N6 bóng+viền (ops/shadow_outline, recipe), FL-2 Flutter icon/tiled/SVG (export/flutter*, FlutterPack).
- [x] N6 shadow-outline recipe — 84 pass, d9d87f4. Chưa: sheet+atlas input, qa.py chưa nhận op.shadow_outline.
- [x] N4 khai báo AI (AI_CREDITS.md trong gói, provenance.ai_disclosure, GET /api/projects/{id}/ai-credits, AiCreditsDialog) — 21 pass, typecheck sạch.
