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
- [x] FL-2 flutter_launcher_icons.yaml + flame_tiled (.tmx từ playable/room_kit) + SVG — 50 pass, dart analyze 0 error. Chưa: SVG có tới exporter không (bundle lọc mime?), TMX chưa chạy thật.
- [~] Giao 3 agent nền: U5 chế độ Đơn giản (app), P4+P5 RunsView phân trang + step.log theo run (core ws + app), N2 ổn định anim (ops/anim_stabilize).
- [x] U5 chế độ Đơn giản/Nâng cao — typecheck sạch, d20c894 (main thêm: prompt/idea luôn hiện).
- [x] P4+P5 RunsView tải thêm theo trang (500) + ws subscribe_runs lọc step.log — 26 pass, typecheck sạch.
- [x] N2 anim-stabilize recipe — 64 pass.
- [x] Chủ nhân báo gen-pose-line xong → xoá .gpu-lock (GPU 761MB/12GB, 0%).
- [~] Agent live game-slice (đích flutter) + kiểm visor + dart analyze gói flutter; agent tự giữ .gpu-lock.
- [x] Live game-slice 15/15 node succeeded ~21,4 phút, không sửa code. Visor: 36 frame không lộ mặt (main xem scratchpad/gs/heads.png). Gói flutter dart analyze lib 0 issue. Output: scratchpad 0bc24b1f…/scratchpad/gs/ (v/, pack_out/default-1.0.0/flutter).
- [ ] Lỗi chất lượng live: walk gần như đứng yên (main xem cs_walk.png); idle 4 tư thế lặp 3; attack nhỏ hơn idle (bbox 160 vs 234); cỏ concept dính chân; minimap nhãn "SpriteSheet"/"SamplePropCleanSw…"; workflow để version draft → pack phải approve tay.
- [~] Chủ nhân chốt: sửa prompt video, chấp nhận mất cache. Giao ANIM-1 (executors.py anim prompt + live idle/walk/attack, giữ .gpu-lock) và LIVE-FIX CPU (cỏ dính chân, scale attack, nhãn minimap, auto-approve game-slice).
- [x] LIVE-FIX bf849ba (ground_strip + cutout node, engine_pack v4 median scale, minimap labels=auto, flows auto_approve) — 163 pass; regen agent docs recipes.md.
- [x] ANIM-1 prompt walk/idle theo pha bước (adapters/anim_motion.py GUIDES, executor v2 → mất cache) + anim_loop lấy nhiều chu kỳ nguyên. Walk diff TB 1742→9874 (main xem scratchpad/an/cs_new_walk.png: chân xen kẽ rõ). Idle 4→8 tư thế, vẫn nhẹ. Slash: frame 9–12 kiếm cong/dài + nhiễu màu (main xem cs_new_slash.png) — cần xem tiếp.
- [~] ANIM-2 (attack prompt + live slash + rerun full game-slice kiểm cutout/scale/minimap/auto_approve, giữ .gpu-lock); N5 GDD→batch (api/gdd.py, app dialog); full pytest nền → scratchpad/full_pytest.log.
- [x] N5 GDD→plan (Opus JSON schema)→GddPlanView xác nhận→flow-batch + recipe-batch trong dự án — 14 pass, typecheck sạch; plan lưu RAM.
- [x] Full pytest (trừ test_anim_video đang sửa): 1515 pass, 5 skip, 356s.
- [~] N3 emotion-portraits (live nếu không có .gpu-lock) + N7 region-edit (core + RegionEditDialog, không live) giao agent.
- [x] N3 emotion-portraits (live OK, main xem sheet: 7 biểu cảm rõ, cùng người; neutral hơi nhỏ) + N7 region-edit (versions.parent_id, RegionEditDialog; chưa live) — 92 pass, typecheck sạch.
- [~] 4 agent song song (chủ nhân cho tăng lên 4): ANIM-2 (GPU), KT-1 compile Kotlin, QA-UI mock + Playwright, FU-1 (portrait scale, qa shadow_outline, shadow-outline sheet+atlas).
- [x] KT-1 Kotlin compile: AGP 8.13.2/Kotlin 2.4.21/BOM 2026.05.00 compileDebugKotlin + processDebugResources sạch (scratchpad/kt). Fix tiền tố số → asset_ trong AndroidPack.
- [x] FU-1 portrait match_scale, qa nhận op.shadow_outline, shadow-outline nhận sheet+atlas — 52 pass.
- [x] QA-UI mock+Playwright (scratchpad/qa/shots): sửa sidebar mode, GoalCards auto-fit, nút Tuỳ chọn nâng cao mousedown.prevent, viewer toolbar wrap.
- [x] ANIM-2 slash/cast GUIDES (kiếm cứng, không trail), executor v3. Live game-slice lần 2 (scratchpad/a2/): cỏ hết, gói engine attack cùng cỡ (447/444/434), minimap sạch, auto_approve 114/114, flutter dart analyze 0 issue. Main xem a2/cs_full.png: idle ổn; walk nhìn trực diện chân rất ít; attack kiếm thẳng nhưng dài, nhân vật nhỏ trong cell; sheet flutter attack nhỏ ~27% (mass_box tính cả kiếm, ops/anim_video.py).
- [x] ANIM-3 anim cell scale theo thân (body_height opening + q30), ô nới tối đa 2x khi kiếm tràn, report cell/body_px, op.anim_loop v2 — attack thân 228 vs idle 229 (scratchpad/an3/). 100 pass export/pack. Rủi ro: ô attack 372 ≠ size; consumer phải đọc report cell/pivot.json.
- [x] UI-2 viewer toolbar 1 hàng (container query), header icon + ellipsis, dashboard grid, region canvas fit, BundleMenu hint wrap, mock PAGE_SIZE override — typecheck sạch, ảnh scratchpad/qa/shots2/.
- [x] Handoff mới: .planning/HANDOFF-2026-11-02-2158.md

## Phiên 2026-11-03 (sau HANDOFF-2026-11-02-2158) — chủ nhân: "làm liên tục", tiếp lượt 11 (tự test + commit + push, không trailer)
- [~] LIVE-3 (GPU, giữ .gpu-lock): game-slice lần 3 concept nhìn 3/4 + kiểm ô anim thay đổi trong gói flutter + live region-edit.
- [~] GDD-LIVE (CPU): POST /api/gdd/plan với Claude thật + xem màn /gdd.
- [~] APP-LIVE (CPU): mở Electron thật với core thật, chụp màn chính.
- [~] CPU-REAL: anim-stabilize/shadow-outline/pixel-cleanup/ground-strip qua engine trên sheet thật a2/. 4 agent chạy nền.
- [x] GDD-LIVE: 2 GDD thật (Opus) 14 mục mỗi plan ~34s ~$0.49, 0 recipe ảo/input sai; màn /gdd ổn (scratchpad/gdd/shots). Timeout core 300→240s — d823e9b. Nợ: Claude thêm filler (audio/weather/font).
- [x] APP-LIVE: electron-vite dev + CDP 9333, mọi màn chính OK (scratchpad/app/shots). Sửa TDZ validateSeq /flows/new — 1a2b3a4. Nợ: gợi ý đè node canvas trống; PackDialog hiện ô Unity khi không chọn Unity.
- [~] FIX-S: lọc audio + siết prompt GDD, gợi ý canvas, ô Unity PackDialog.
- [x] FIX-S lọc audio GDD + gợi ý canvas + ô Unity — 8 pass gdd, typecheck sạch.
- [x] CPU-REAL (scratchpad/cpu/): stabilize walk chân 6.1→1.9px; shadow-outline atlas đúng ô; pixel-cleanup 32 màu thô; ground-strip xoá oan giáp xanh → FEET_BAND 0.35, v2 — 81477ea. Nợ: ground-strip cỏ sát chân vẫn ăn 100–400px đế giày (đánh đổi, chưa sửa).
- [~] STAB-2: stabilize chân theo thân (kiếm chĩa xuống làm lệch), bóng blob bám chân thật thay pivot atlas, pixel-cleanup 64 màu cho ô ≥128.
- [x] STAB-2 stabilize chân theo thân (attack đáy 235..242→241), bóng blob bám chân thật, pixel-cleanup colors auto 16/64 — 78 pass; scratchpad/stab2/. Nợ: op.pixel_art (sprite-8dir-pixel) vẫn colors 16 tối đa 32.
- [x] LIVE-3 (scratchpad/g3/): game-slice 16/16 ~24 phút, concept nghiêng; thân idle/walk/attack 228/233/231 (body_px ~228) → attack cùng cỡ; cell 256/272/384; walk chân xen kẽ rõ (main xem); visor kín. Consumer ô đổi cỡ: flutter dart analyze 0, android/ios/godot/unity đúng. region-edit live OK (g3/re/compare.png, parent_id đúng). Lỗi: attack frame cuối không về stance, frame 5/7–9 kiếm nhoè; unity/godot thừa thư mục anim version cũ.
- [~] ANIM-3 anim_loop v3: clip không loop cắt về stance + chọn frame nét nhất lân cận. PACK-1 chỉ lấy version approved mới nhất mỗi clip + dọn thư mục gói.
- [x] PACK-1 latest_per_asset (tên+recipe, bỏ khi include tường minh) + list_packs superseded — 212 pass, typecheck sạch.
- [x] ANIM-3 anim_loop v3: attack end 49→21 (giống frame0 0.76→0.89), frame ít ghost (29–61→1–19 px); walk/idle giữ nguyên — 121 pass; main xem scratchpad/anim3/after_attack.png: giơ→chém→về stance.
- [x] 01:12 Handoff mới: .planning/HANDOFF-2026-11-03-0112.md (full pytest 1565 pass)
- [~] LIVE-4 chạy lại game-slice (cache GPU) xem attack v3 + gói sạch _2/_3; PIX-2 op.pixel_art colors auto 16/64.
- [x] PIX-2 op.pixel_art colors auto v2 + 2 recipe pixel options tới 64 — 246 pass.
- [~] Chủ nhân: đề xuất model + chức năng theo cấu hình máy, đèn đỏ/vàng/xanh khi chọn chức năng. Plan `.planning/PLAN-2026-11-03-hw-fit.md`. HW-1 core (/api/hardware, /api/fit, manifest hw) + HW-2 app (FitBadge, Settings "Phù hợp với máy") song song theo contract.
- [~] Chủ nhân: thêm 3 tính năng mới hợp công cụ hiện có → agent RESEARCH đề xuất 6 ứng viên, main chọn 3.
- [x] RESEARCH 6 ứng viên → chọn B exporter Flutter đủ bộ → A shader Flutter + đèn 2D → C demo Flame (tuần tự). Plan .planning/PLAN-2026-11-03-new3.md. [~] NF-B giao sonnet.
- [x] HW-1 /api/hardware + /api/fit + manifest hw — máy này 92 xanh/4 vàng/0 đỏ, flow 4/1/0; giả lập không GPU 63 đỏ.
- [x] HW-2 FitBadge/FitPanel/xác nhận đỏ — typecheck sạch; ảnh scratchpad/hw/shots (main xem settings_fit, newrun_red dark). Chủ nhân: commit từng phần + cho build để test.
- [x] GDD-2 plan lưu SQLite gdd_plans + GET /api/gdd/plans + lọc flow audio — 17 pass.
- [x] HW-3 Electron thật + core thật: số liệu khớp /api/hardware, 96/5/0 khớp /api/fit, fit 644ms cold, 0 console error (scratchpad/hw3/shots).
- [x] GS-3 ground-strip v3: thân mất 141–233→17–99 px, cỏ thật sạch giữ giày (main xem gs3/after_real.png) — 18 pass.
- [~] Chủ nhân: áp skill web-design + stageshow + rule CLAUDE.md để UI app đẹp, clean, dễ dùng hơn.
- [x] LIVE-4 (scratchpad/g4/): 16/16 ~26 phút; attack về stance (IoU 0.89 frame0), thân attack = idle ~229; unity/godot hết _2/_3; dart analyze 0. Lỗi: cache GPU không hit khi chạy lại cùng input → [~] CACHE-1 điều tra. [~] UI-1 design system (opus).
- [x] CACHE-1: không lỗi — concept-2d/sprite-anim-video không pin seed nên cố ý không cache (adapters/comfy/executor.py:109). Thêm test. Chờ chủ nhân: tuỳ chọn dùng lại seed khi chạy lại (mặc định giữ hành vi cũ).
- [x] NF-B exporter Flutter: tile_anim, autotile+.tmx, vfx, weather, ui_screen, card_set, minimap (bỏ trim/decal) — 239 pass, dart analyze 0, flutter test 7/7 (scratchpad/flv/nfb). [~] NF-A shader Flutter + đèn 2D.
- [x] NF-A shader Flutter 6 hiệu ứng + lit_sprite 4 đèn — 134 pass, dart analyze 0, flutter test 9/9 (golden khớp CPU <0.1), impellerc biên dịch 7 .frag (scratchpad/flv/nfa). Chưa thử thiết bị Impeller thật. [~] NF-C demo Flame.
- [x] UI-1 design system (tokens light/dark, font local, 15px, pill/card/sidebar) — typecheck+build sạch, không cuộn ngang 6 màn (scratchpad/ui1/shots). [~] UI-2 A/B.
- [x] UI-2A Dashboard/NewRun/RunDetail/Runs/Gates — typecheck+build sạch, không cuộn ngang (scratchpad/ui2a). Plan voice: .planning/PLAN-2026-11-03-voice.md (chủ nhân mở lại voice).
- [x] UI-2B Projects/ProjectDetail/Gallery/Settings/FitPanel/Recipes/Flows — typecheck+build sạch (scratchpad/ui2b). [~] UI-3 stageshow minh hoạ + Electron QA cuối.
- [x] NF-C demo Flame: pytest 325 pass, demo từ gói thật analyze 0 + flutter test pass + APK debug build OK (scratchpad/nfc/demo_app). Lỗi: vệt dọc mảnh giữa ô tile; chưa có option API/UI. [~] NF-C2.
- [ ] Cuối đợt (chủ nhân): viết lại README ngắn dạng liệt kê + release note, bump 1.4.0 (changelog có 1.2/1.3 chưa tag; GitHub chỉ có v1.1.0), npm run dist, gh release v1.4.0 (Setup exe + Portable zip).
- [x] 02:29 Handoff mới: .planning/HANDOFF-2026-11-03-0229.md (4 agent đang chạy: NF-C2, UI-3, VOICE-V0, DEMO-5)
- [x] 02:4x NF-C2 flutter_demo option + vệt tile — golden scratchpad/nfc2/golden.png sạch, pytest 18 pass, typecheck sạch, commit 5ed9408
- [x] 03:1x DEMO-5 live 21 recipe 2D chưa thử: 21/21 OK (scratchpad/demo5/overview.png), không sửa repo. Yếu: decal wall_cracks, mist đặc, time-season màu nặng/winter/dusk seam, icon nhỏ+potion nâu, parallax sương. GPU dọn, .gpu-lock xoá.
- [~] DEMO-6 chỉnh 5 recipe yếu (sonnet, nền, giữ .gpu-lock khi chạy) → scratchpad/demo6/compare.png
- [x] 03:2x UI-3 minh hoạ stageshow GettingStarted + empty Library/History/Projects (light/dark webp), typecheck sạch, ảnh scratchpad/ui3/shots/cmp_*.png, commit 88e3af6
- [x] 03:3x VOICE-V0 trang nghe L:/AI/tts-lab/voice-compare.html (132 clip, VieNeu/Kokoro/edge/Chatterbox; Chatterbox không có VI) — chờ chủ nhân chọn giọng
- [~] REL-1 README + release note + bump 1.4.0 + changelog (sonnet, nền)
- [x] 03:4x REL-1 README 80 dòng + .planning/release-1.4.0.md + bump 1.4.0 + changelog, test_version 2 pass, commit 61e8230. Chờ DEMO-6 → full pytest → dist → tag
- [x] 04:2x DEMO-6 decal nứt thật, mist mỏng, season nhẹ + winter tuyết, icon khung đơn to hơn (còn dao động màu), parallax hết sương; scratchpad/demo6/compare.png; 29+247 pass; commit 1e8f9d0
- [~] REL-2 full pytest → npm run dist + dist:portable → tag v1.4.0 → gh release
- [x] 04:5x REL-2 full pytest 1625 pass/6 skip; npm run dist OK; portable zip 223.6MB; tag v1.4.0; release https://github.com/doanvu2000/AI-Asset-Studio/releases/tag/v1.4.0
