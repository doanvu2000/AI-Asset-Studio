# PROGRESS — nhật ký làm việc (resume sau crash)

Session mới: đọc `.planning/HANDOFF-2026-10-18-2034.md` rồi file này TRƯỚC, rồi `git log --oneline -10` + `git status`. Cập nhật mỗi khi xong 1 bước / commit.
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
13. [x] Viewer app (`71a1886`): GLB/sheet/Spine + catalog kinds rig. Kiểm GLB với core thật; sprite+Spine mới kiểm mock.
14. [x] P7 chuẩn bị đóng gói (`0b2d3e2`): spec PyInstaller onedir, paths.py frozen-aware, /api/settings/tools + UI,
   electron-builder NSIS, BUILD.md. CHƯA BUILD (rule) → chủ nhân chạy `cd app && npm run dist`, lỗi thì báo con.
   Claude steps ở bản đóng gói cần `claude` trên PATH (đã bỏ claude.exe 260MB khỏi bundle). pytest 199.

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
- 16:55 — R5-mv (`73e8808`, `bc8e8e6`): Qwen trái/sau/phải + Pixal3D multi-view → lưng + gậy đúng ở 8 hướng, tốt hơn R5.
  Cần model `pixal3d_multiview_int8_convrot.safetensors` (đã tải về F:\work\AI\comfyUI_models\diffusion_models, 5.3GB)
  + workflow `img2mesh_trellis2_mv_textured` (ngoài repo). Qwen đôi khi vẽ góc 90° nằm ngang → gate `check` duyệt góc.
  RAM: multi-view ăn ~24GB host → `_restart_before` khởi động lại ComfyUI trước bước mesh. Live r5mv5 OK 325s
  (mesh 201s), không MemoryError; Qwen lại vẽ góc trái nằm ngang nhưng mesh vẫn tốt hơn single-view.
- Auto-check góc Qwen (`15b35fb`): op.view_check + views.qwen_checked (sinh lại tối đa 3, rồi bỏ góc), gate `enabled`,
  R5-mv `review_views` mặc định tắt. Live 3/3 succeeded, 2 lần sinh lại 1 góc. pytest 212.
  Mở: wizard ra mesh hỏng dù góc qua check (các góc lệch nhau: gậy đổi tay) → cần check nhất quán (màu/part hoặc VLM).

## Việc còn mở (ưu tiên)
1. [x] Chủ nhân đã build (17:17): installer 140.7MB. Lead smoke test core frozen: READY, 13 recipe, 32 kind, /settings/tools 200, shutdown OK.
   Tiếp: cài thử installer, chạy 1 run thật trong app đóng gói.
2. Check nhất quán giữa các góc Qwen (VLM Claude haiku so 4 góc) cho R5-mv.
3. Sprite + Spine viewer mới kiểm mock, chưa kiểm với core thật.
4. R7 reviewer khắt khe (thường đủ 3 vòng); procedural_3d không giữ lane BLENDER.
5. Push lên remote khi chủ nhân bảo.
- 17:30 — Bản portable (`5617ec4`): cài ở L:\AI\AI Asset Studio Portable (465MB), file marker `portable` → data/ + userdata/ cạnh exe. Đã mở thử: core lên, không ghi %APPDATA%. Build lại: `cd app && npm run dist:portable` rồi chép win-unpacked.
- 17:45 — Fix bản đóng gói: IPC app:// bị coi untrusted (`1364404`, đã chép vào portable); Claude step lỗi claude.CMD →
  `claude_exe` + cli_path (`00f3122`, CHƯA build vào portable). Đã đăng ký MCP asset-studio cho Claude (user scope) và Codex
  (~/.codex/config.toml, backup .bak-asset-studio) trỏ bản portable. Agent devlog (logs/ + /api/diagnostics + panel
  Nhật ký chẩn đoán) đang chạy → xong thì build core + app, chép vào portable (chủ nhân phải thoát app).
- 18:05 — Devlog (`90402ab`), resize + UI pass (`a8107c4`), second-instance + icon (`3556e57`). Build core+app mới:
  test bản portable tạm: core kết nối, panel Nhật ký chẩn đoán, claude_exe ok, bước claude.brief chạy thật OK.
  Đang chờ chủ nhân thoát app để chép vào L:\AI\AI Asset Studio Portable. Agent khảo sát UI → .planning/UI-AUDIT.md (đang chạy).
- 19:xx — Bắt đầu nâng cấp UI theo UI-AUDIT (`.planning/PLAN-ui-upgrade.md`): 4 agent song song A graph · B run+gate ·
  C export+shell · D vocab/lists/art Codex. Lead chờ xong → typecheck + pytest + chụp mock → commit theo nhóm.
- Agent C xong: export blob (`POST /api/blobs/{hash}/export`, main AssetExporter whitelist exports/app), AssetActions,
  Gallery gom asset, Lightbox footer, CoreOffline/Banner, Settings Chọn…, taskbar+Notification. pytest 230. Mở: xuất theo
  preset Unity/Godot, xuất cả bộ Spine, chưa chụp màn.
- Agent A xong: fitView+ResizeObserver, minimap ≥13 node, connector theo bước đích, sheet xác nhận thay confirm, SelectMenu. Lead sửa graph.ts sparse columns (NaN khi recipe rỗng).
- Agent A xong: fitView+ResizeObserver, minimap ≥13 node, connector theo bước đích, sheet xác nhận thay confirm, SelectMenu. Lead sửa graph.ts sparse columns (NaN khi recipe rỗng).
- Agent B xong: result hero, GatePicker/GateApprove inline, errorAdvice callout, segment progress, menu Huỷ. Lead: core retry_step cho phép 'tạo lại' bước nguồn (succeeded/cached) khi gate sau đang chờ, meta fresh bỏ qua cache + test. Mock retry cần sửa sau khi D xong.
- Agent D xong: format.ts stepLabel/errorLabel/directionLabel, thumbnail RunRow (core preview_hash), sidebar badge, Dashboard
  + GettingStarted, NewRun lưới quy trình + chế độ Nhiều prompt, BatchDetail stat lọc, Ctrl+K palette + `?`, token, 6 art Codex.
- Lead: mock retry "tạo lại", GatesView lọc `?batch=`, chữ 10.5px/recipe sót, review fix (phím gate không cướp Enter trên
  nút/overlay, `?` guard, đuôi file xuất, isDestroyed drag, prepare retry, xoá cờ fresh). typecheck sạch, pytest 231+.
  Chụp mock kiểm: Tổng quan (sáng/tối), Thư viện, Tạo mới, run hoàn tất/chờ chọn/lỗi, Tạo lại ảnh OK.
- COMMIT BỊ CHẶN (classifier) → working tree chưa commit, chờ chủ nhân. Đề xuất 5 commit: core / export+shell / graph /
  run+gate / vocab+lists+art.
- Mở: xuất theo preset Unity/Godot + xuất cả bộ Spine; kiểm app thật (kéo ra Explorer, taskbar, Notification);
  thẻ Thư viện thừa khoảng trống dưới caption; Đợt 3 motion (check morph, shared-element) chưa làm.
- 20:26 — Viết HANDOFF-2026-10-18-2026.md. Working tree 76 file chưa commit, chờ chủ nhân.
- [x] 20:34 Handoff mới: `.planning/HANDOFF-2026-10-18-2034.md` (thay 2026). Kill Electron mock sót PID 10956.
- [x] 21:xx (session mới) — Xuất thư mục: core `store/bundle.py` + `GET/POST /api/steps/{id}/export` (op.sheet repack theo
  preset → `<asset>_sheet.png/.json` + `frames/`; Spine/khác chép mọi output giữ tên blob để .atlas trỏ đúng png),
  `presets.annotate_sheet_meta` dùng chung với op.sheet, test `test_step_export.py`. Main `assets.ts` bundleOptions/exportBundle
  (dir phải nằm trong exports/app), IPC `studio:asset:{bundleOptions,saveBundle,revealBundle}` (chọn thư mục cha → `fs.cp`).
  Renderer `BundleMenu.vue` trong AssetActions (prop `stepId`; RunDetail truyền bước op.sheet/op.spine_export). Mock không
  có tệp thật → nút ẩn ở mock. Thư viện: nút Duyệt/Từ chối nổi trên ảnh (bỏ dải 40px trống). Motion: lightbox mọc từ
  thumbnail (FLIP WAAPI, prop `origin`), stagger 30ms thẻ Gần đây (check morph + shake lỗi StepChip đã có sẵn).
  typecheck sạch, pytest 235/4 skip, chụp mock Thư viện + lightbox OK. CHƯA commit (chờ chủ nhân).
  Còn mở: kiểm xuất thư mục với core thật; shared-element run → result hero.
- [x] Commit 5 nhóm `62d4776..a75d5d5` (chủ nhân cho phép). Build core (PyInstaller) + app (`electron-builder --win dir`).
  Test bản đóng gói trong scratchpad (`pkg/` + marker portable, STUDIO_DATA_DIR = bản sao data live r5mv5 / r6b):
  "Xuất cho engine" + menu preset hiện đúng; API export Godot/Unity trên sheet thật đúng thứ tự + pivot_norm; Spine
  7 tệp, `hero.atlas` trỏ `hero.png`; kill core → supervisor tự spawn lại ~2s; `restartCore` IPC OK; run thật
  `sprite-8dir-glb` (Blender, 28s) xong trong app. Chưa kiểm bằng tay: kéo ra Explorer, taskbar progress, Notification
  (CDP giả focus nên notify bị chặn đúng thiết kế). Lưu ý: đổi `location.hash` từ CDP không đổi trang → dùng click.
- Chép vào `L:\AI\AI Asset Studio Portable`: app đã chép; `resources\core` bị khoá bởi 3 MCP `studio-core.exe mcp` (cha
  claude.exe) → vòng chờ nền tự chép core khi chúng thoát. Chưa xong thì: thoát các phiên Claude Code rồi
  `robocopy app\release\0.1.0\win-unpacked\resources\core "L:\AI\AI Asset Studio Portable\resources\core" /E`.
- [x] Check nhất quán góc Qwen bằng Claude (`views.qwen_checked` param `consistency`, `consistency_model`=haiku; ViewSpec
  `expect`): góc qua silhouette → 1 call Claude (front + các góc mới, JSON schema) → góc "inconsistent" sinh lại như
  góc hỏng; Claude lỗi → cho qua (fail-open, report ghi `consistency.error`); meta `claude_cost_usd`. R5-mv input mới
  `consistency_check` (mặc định bật). Test `test_view_consistency.py`; pytest 240/4 skip.
  Live judge trên ảnh cũ (không GPU, ~$0.0025/lần, 8–10s): bản prompt đầu haiku loại nhầm mặt sau pháp sư (lẫn
  trái/phải khi nhìn từ sau) → thêm bảng ánh xạ trái/phải vào prompt → đúng 100% ca thật (2 lần), ca âm (lật gương
  góc sau = gậy đổi tay; góc của nhân vật khác) bắt đúng 2/2. Kết luận cũ "gậy đổi tay" ở vc/live2: góc Qwen thật ra
  đúng → mesh hỏng do chỗ khác (TRELLIS), chưa rõ.
- Live R5-mv có check Claude (`scratchpad/r5mv_vlm`, `e2e_r5mv.py --only mv`): views 84s, 3/3 đạt lần 1, Claude giải
  thích đúng phía gậy, $0.0023. Mesh FAIL 2 lần: `Pixal3DMultiViewConditioning … not enough memory: 8589934592 bytes`
  — Windows commit 50.8/67.8 GB khi ComfyUI đã tắt (pagefile trên C: nhỏ, C: gần đầy) → không phải lỗi code. Cần chủ
  nhân: tắt bớt app / khởi động lại máy, hoặc thêm pagefile ở ổ khác (L:/F:). Chạy lại: lệnh trên.
- UI: `components/ViewsReport.vue` (khối "Kiểm tra góc" trong inspector bước views.qwen_checked: thumbnail, Đạt/Đã
  bỏ góc, chip lý do từng lần, lý do Claude ở tooltip). typecheck sạch, CHƯA chụp (mock không có report; cần build).
- Chưa commit: views.py/kinds.py/recipe R5-mv/test_view_consistency.py/test_p7_mv.py + ViewsReport.vue + RunDetailView.
- [x] CHỦ NHÂN CHỐT: mọi bước Claude (viết prompt/brief, check góc, task, procedural code+QA, blender.agent) mặc định
  **Opus 5.5**, bỏ haiku. `adapters/claude.py`: `MODEL_IDS` alias→id ghim (`opus`→`claude-opus-5-5`), `DEFAULT_MODEL`
  = opus, `build_options` map qua `model_id`; kinds enum `MODEL_CHOICES` default opus; recipe brief/spine/procedural →
  opus. Live: brief Opus 13s $0.026 prompt chi tiết; check góc Opus ~$0.07–0.09/lần (haiku $0.0025), đúng 100% ca
  thật + ca âm. pytest 240/4 skip, typecheck sạch. Chưa commit.
- [x] 22:22 Handoff mới: `.planning/HANDOFF-2026-10-18-2222.md` (thay 2034).
- [x] 23:xx (session mới) — Pagefile F: 64 GB cố định (chờ reboot). Commit `de8210c` (check góc + Opus + ViewsReport).
- [x] Việc 6–10 trong lúc chờ reboot:
  - R7 reviewer: `QA_SYSTEM` có thang điểm, low-poly phẳng không tính lỗi, `issues` chỉ lỗi chặn (≤4), thêm `polish`
    (không hạ điểm), `accept_score` mặc định 7. Chấm lại ảnh r7a cũ bằng Opus: vòng 1/2 còn đúng 1 lỗi chặn thật
    (mặt đen / nắp chồng thân), vòng 3 = 7 ok (cũ 5/6/5, 7 mục lặt vặt). pytest 240/4 skip.
  - Shared-element run → result hero: `composables/motion.ts` `rememberOrigin/takeOrigin/flyFromOrigin` (ghost ảnh
    thumbnail bay vào ô kết quả rồi mờ; tôn trọng reduced-motion; origin quá 1.2s/2s bỏ). Gắn ở RunRowItem (chỉ khi có
    preview_hash), tile Gần đây ở Tổng quan, RunDetailView `:ref="onHeroStage"`. Kiểm mock qua CDP: ghost hiện ~150ms
    sau click, ~500ms rồi xoá; run chờ duyệt không có hero → không bay. typecheck sạch.
  - Kéo ra Explorer: code đúng (prepare khi hover ở cả 2 nơi gọi) — còn chủ nhân thử tay + taskbar + Notification.
  - Đợt 3 UI-AUDIT: #18 (stat lọc + "Duyệt cả lô" mở hộp duyệt lọc theo lô = duyệt tuần tự), #23 (Chọn…, tách mục
    Preset, phụ đề caption) đã có → Đợt 3 coi như xong.
  - Mesh pháp sư (vc/live2): workflow giống hệt live1 (hiệp sĩ 2D, tốt) và live3 (yêu tinh, tốt), chỉ khác seed + ảnh;
    4 góc nhất quán. Không phải style 2D. Giả thuyết: (a) seed xấu ở Pixal3D/TRELLIS, (b) crop từng góc riêng
    (ImageCropToMask theo bbox mỗi ảnh) lệch tâm vì gậy dài. Cần GPU: chạy lại mesh cùng 4 ảnh với 2–3 seed; nếu
    seed → thêm check chất lượng mesh + tự sinh lại.
- [x] 23:16 Handoff mới: `.planning/HANDOFF-2026-10-18-2316.md` (thay 2222).
- [x] 2026-10-19 (session mới, chủ nhân đang dùng GPU → không chạy GPU). Máy đã reboot: `F:\pagefile.sys` 65536 MB
  đã cấp phát. Giả thuyết (b) crop lệch tâm ở mesh pháp sư (vc/live2) → BÁC: đo bbox 4 góc, cạnh dài 886–898 px
  (lệch ≤1.5%, live3 tốt còn lệch 5%), tâm x 0.48–0.52 → ImageCropToMask cho cùng tỉ lệ. Còn giả thuyết seed.
  Script sẵn: scratchpad `81ce8457-.../scratchpad/seed_probe.py` (chạy lại mesh live2 với N seed mới qua
  run_workflow.py, GLB ra ComfyUI `output/seedprobe/`). Chạy khi GPU rảnh: bật ComfyUI rồi
  `cd core && uv run python <scratchpad>/seed_probe.py 3`. Chép core Portable vẫn chặn (10 studio-core.exe đang chạy).
- [x] Commit `2072734` (R7 reviewer + shared-element). GPU rảnh (chủ nhân cho phép).
- [x] Live R5-mv (`scratchpad 81ce8457/r5mv`): 417s, views 3/3 đạt lần 1 (79s), mesh 183s KHÔNG còn lỗi RAM (pagefile F),
  sheet 8 hướng pháp sư đẹp.
- [x] Thử seed vc/live2 (cùng 4 ảnh): 3 seed mới → 1 đẹp (5079…), 2 hỏng; seed gốc hỏng → lỗi do SEED.
  Metric IoU silhouette (render Blender 4 góc vs ảnh vào, `viewcheck.silhouette`): tốt min 0.82–0.90, hỏng ≤0.66.
- [x] `mesh.checked` (`adapters/comfy/mesh_check.py`): sinh mesh qua workflow comfy → `render_dirs` 4 góc elevation 0 →
  IoU với ảnh vào theo `azimuths` (key ảnh workflow → độ, theo slot còn lại sau `_drop_inputs`) → min < `min_iou` 0.75
  thì sinh lại (seed mới, tối đa `attempts` 3); hết lượt `on_fail` best|fail; render lỗi → cho qua. Report
  `mesh_report.json` (iou từng góc + ảnh render). Recipe R5-mv bước mesh dùng `mesh.checked`. Kinds + nhãn
  "Dựng mesh 3D" + `MeshReport.vue` (khối "Kiểm tra mesh" trong inspector). Test `test_mesh_check.py` + test_p7_mv;
  pytest 246/4 skip, typecheck sạch. Đang chạy live trên 4 ảnh pháp sư hỏng (`scratchpad 81ce8457/mc`).
- [x] Live `mesh.checked` trên 4 ảnh pháp sư hỏng (`scratchpad 81ce8457/mc`): lần 1 min IoU 0.56 → sinh lại, lần 2
  min 0.90 đạt; run 554s; sheet 8 hướng đẹp. CHƯA commit.
- [x] Vòng kiểm UI trên app dev + data thật (`scratchpad 81ce8457/mc`, CDP 9354): khối "Kiểm tra mesh" hiện đúng
  (Lần 1 Lệch hình 56% / Lần 2 Khớp 90%). Sửa: ẩn ô JSON `report` thô ở bước có khối báo cáo riêng (views/mesh),
  StepChip ghi "· tự làm lại N lần" / "· sinh lại N góc", nút "Hiện trong thư mục" → "Mở thư mục" (hàng nút xuất
  hết rớt dòng). `mesh.checked` lưu ảnh render kiểm tra vào report (chưa thấy live — run trước sửa). pytest 246/4,
  typecheck sạch. CHƯA commit.
  Còn mở (đề xuất): thẻ Tạo mới mô tả = chuỗi tên bước, chưa nói ra cái gì/mất bao lâu; tile Gần đây của sheet 8
  khung quá nhỏ (nên hiện 1 khung).
- [x] Chọn theme (chủ nhân hỏi): Cài đặt → Hệ thống → "Giao diện" (Theo Windows / Sáng / Tối) + Ctrl K "Giao diện: …".
  Main `nativeTheme.themeSource` (CSS prefers-color-scheme, Mica, title bar đổi theo), lưu `userData/ui.json`, đọc
  trước createWindow; IPC `studio:getTheme/setTheme`; renderer `composables/theme.ts`. Kiểm mock qua CDP: đổi
  sáng/tối tức thì, ui.json ghi đúng. typecheck sạch. CHƯA commit.
- [x] Thẻ Tạo mới: câu "ra cái gì" (`assets/art` `recipePitch`), nhãn "Khuyên dùng" (sprite-8dir-3d-mv), "Có bước bạn
  chọn ảnh"; chuỗi bước chuyển vào tooltip. Thumbnail lượt chạy bỏ qua cổng `sheet` → khung mặt trước (core
  `run_preview_hashes` + mock + test). Khung "Thông số máy" trên Tổng quan (`DeviceCard.vue`, main `device.ts`
  IPC `studio:deviceInfo`: GPU/VRAM/tải/nhiệt qua nvidia-smi, RAM, CPU; poll 3s khi cửa sổ hiện; chủ nhân bảo bỏ ổ đĩa).
- [ ] Chủ nhân thấy style icon/art xấu → đang sinh 4 hướng style bằng Codex (A glyph SF / B pixel / C diorama
  low-poly / D mực+màu nước) × 3 chủ đề, `scratchpad 81ce8457/style/`, ghép bảng cho chủ nhân chọn.
- [x] Mẫu nhanh (chủ nhân yêu cầu): `QuickPrompts.vue` trên ô prompt/idea (chế độ 1 lượt) — 12 chủ đề (cây cối, bụi
  cỏ, đá, pha lê, nhà, vũ khí, rương, bình thuốc, thùng, nhân vật, quái vật, linh thú) + 10 phong cách (trung cổ,
  anime, tu tiên, Nhật cổ, chibi, hoạt hình, pixel, low-poly, dark fantasy, sci-fi). Nhãn Việt, text tiếng Anh;
  chip chỉ sửa phần của nó (chủ đề đầu câu, phong cách cuối câu), giữ chữ người dùng gõ giữa. Kiểm CDP OK.
- [x] 12 mẫu style xong: `scratchpad 81ce8457/style/compare.png` (hàng A glyph / B pixel / C diorama / D mực; cột sprite,
  rương, concept). Đề xuất C (diorama low-poly) cho art quy trình + empty state, giữ icon nét lucide cho thanh bên.
  CHỜ chủ nhân chọn → sinh lại 13 art quy trình + 6 art empty/onboarding theo style chọn.
- [x] Prompt tiếng Việt (chủ nhân chọn "cả hai"): thử Z-Image cùng seed VI/EN — VI mất ý chuyên biệt (rương → hộp,
  tu tiên/linh khí mất), anime đơn giản thì OK. Core `adapters/translate.py` `Translator` (Claude Opus, schema, cache
  theo text) + `POST /api/translate`; app: nút "Dịch ngay để xem" dưới ô prompt khi có dấu tiếng Việt + tự dịch khi
  bấm chạy (cả lô 1 call), tên lượt chạy giữ tiếng Việt; bỏ qua recipe có bước claude./codex./blender.agent. Live:
  9s, $0.013, dịch đúng xianxia/treasure chest. Test `test_translate.py` + API test.
- [ ] Chủ nhân chọn style D (mực + màu nước) → sinh 18 art nền trong suốt (`scratchpad 81ce8457/artd`, `art_d.py`),
  sẽ crop sát + webp 192 (recipe) / 352 (empty) thay vào `app/src/renderer/src/assets/art/` + map recipe-sprite-8dir-2d-prompt.
- [x] Art style D đã thay: 18 webp nền trong suốt (12+1 recipe 192px, 5 empty/onboarding 352px), cắt sát nội dung;
  `recipe-sprite-8dir-2d-prompt` có art riêng. app-icon giữ nguyên. Chụp mock Tạo mới sáng/tối OK.
  pytest 249/4 skip, typecheck sạch. Mockup chọn style: `.planning/style-mockup/index.html`. TẤT CẢ CHƯA commit.
- [x] 2026-10-19 02:19 Commit `254a327`. Handoff mới: `.planning/HANDOFF-2026-10-19-0219.md` (thay 2316).
- [ ] 2026-10-19 (session mới, chủ nhân đi ngủ, cho tự build + test, chia subagent). Đang: subagent làm mẫu nhanh cho
  chế độ nhiều prompt + ước lượng thời gian trên thẻ Tạo mới; live R7 (`scratchpad 5e0b94bb/r7`); build core PyInstaller.
  Sau đó: typecheck/pytest → build app → chờ Portable thoát rồi chép.
- [x] Build core PyInstaller OK (`core/dist/studio-core`). Live R7 reviewer mới (`scratchpad 5e0b94bb/r7`): 2 vòng
  (6 → 8 đạt), 91s, $0.26, rương đẹp → xác nhận ít vòng hơn. Đang live R5-mv (`scratchpad 5e0b94bb/r5mv`) xem ảnh render MeshReport.
- [x] Subagent xong: mẫu nhanh chế độ Nhiều prompt (chip chủ đề "Thêm dòng", phong cách "Cả lô" nối sau khi dịch;
  `assets/quickPrompts.ts` dùng chung) + ước lượng thời gian thẻ Tạo mới (trung vị ≤20 lượt xong gần nhất, mock có số).
  Kiểm CDP mock OK (`scratchpad 5e0b94bb/new.png`, `batch.png`). typecheck sạch, pytest 249/4 skip. CHƯA commit.
- [x] Build app `app/release/0.1.0/win-unpacked` (kèm core mới). Chép Portable bị khoá: 8 `studio-core.exe` MCP từ
  Portable đang chạy (GUI không chạy). Script `.planning/update-portable.ps1` (`-Wait` chờ rồi chép); đang chạy nền chờ.
- [x] Live R5-mv hiệp sĩ (`scratchpad 5e0b94bb/r5mv`): 336s, views 3/3 đạt lần 1, mesh.checked lần 1 Khớp 93%
  (97/94/97/93), sheet 8 hướng đẹp. App dev trên data thật: khối "Kiểm tra mesh" hiện ảnh render 4 góc + % (đã thấy live).
- [ ] Chép Portable: vòng chờ `update-portable.ps1 -Wait` đang chạy nền (chặn bởi 8 studio-core.exe MCP). Nếu phiên
  này đóng, vòng chờ chết → chủ nhân đóng app + các phiên Claude/Codex rồi chạy
  `powershell -ExecutionPolicy Bypass -File L:\AI\AI-Asset-Studio\.planning\update-portable.ps1`.
  Thay đổi phiên này CHƯA commit (QuickPrompts, NewRunView, mock server, assets/quickPrompts.ts, update-portable.ps1).
- [x] Commit `b0d277b`. Chủ nhân tắt studio-core MCP → Portable đã chép bản mới 02:37 (core + app, gồm mesh.checked,
  dịch VI→EN, theme, art D, mẫu nhanh lô, ETA). MCP asset-studio các phiên cần khởi động lại phiên.
- [ ] 2026-10-19 (session mới, chủ nhân chọn "#1 + #2"): (1) app-icon style D — Codex sinh 3 phương án
  (a tile tím / b tile giấy / c tự do) vào `scratchpad 81b71353/icon/` (`icon_d.py`), chờ chủ nhân chọn rồi
  `app/scripts/gen-art.py icon` → resources/icon.png/.ico/tray.png + app-icon.webp. (2) rà UI-AUDIT còn sót.
- [x] App icon style D: chủ nhân chọn B (tile giấy kem, pha lê tím trên đe) → `gen-art.py icon` ra
  `app/resources/icon.png/.ico/tray.png` + `assets/art/app-icon.webp` 64px (4 góc trong suốt). Ảnh gốc + so sánh:
  `scratchpad 81b71353/icon/`, `icon_compare.png`. `art-manifest.json` vẫn ghi prompt cũ (art D sinh bằng `icon_d.py`/`art_d.py`).
- [x] Rà UI-AUDIT: mục 1–28 đã có trong code (soát lại: thuật ngữ, tìm/lọc thư viện, phím ←/→ lightbox, badge "Của tôi",
  `--st-pending-text`, tooltip phím tắt sidebar, prompt ở hộp duyệt, lưới auto-fill). Chỉ thiếu CTA ở empty state
  Cần duyệt → thêm "Xem lượt chạy" + "Tạo asset mới". Codex lệch trạng thái §6 chỉ có ở mock (core cùng dựa `codex_exe`).
  typecheck sạch. CHƯA commit; Portable chưa có icon mới (cần build lại).
- [x] Commit + push `app: style-D app icon…`. Build app (core không đổi) → chép Portable 07:34 trừ `resources\core`
  (11 studio-core.exe MCP đang giữ; core bản cũ = bản mới nên không cần chép).
- [ ] 2026-10-19 07:4x "làm tiếp việc còn lại": tồn đọng code thấp giá trị → KHÔNG làm: comment YAML mất khi lưu
  recipe từ editor (chỉ 3 recipe gốc có comment, cần thêm ruamel + build lại core); lô chỉ tính 500 lượt gần nhất
  (Portable mới có 1 lượt). Đang live R6 (`scratchpad 81b71353/r6`, GPU rảnh) để kiểm Spine viewer trên core thật.
- [x] Live R6 (`scratchpad 81b71353/r6`): 177s, 19 xương / 15 slot, tư thế gốc IoU 0.9997, Claude bones $0.065.
  App dev trên data thật: Spine viewer render đúng, chuyển idle/relax OK, nút xuất đủ → Spine viewer đã kiểm với core thật.
  Còn mở (cần chủ nhân): mở JSON Spine trong Spine Editor; thử tay Portable (kéo file, taskbar, Notification).
- [x] Chủ nhân bảo làm 2 tồn đọng:
  (1) Lưu recipe từ editor giữ comment YAML: `engine/library.py` `merge_text` (ruamel round-trip, gộp doc mới vào text
  cũ; list step khớp theo id, comment giữa các step gán lại theo item). Thêm dep `ruamel-yaml`. Giới hạn: flow map viết
  nhiều dòng bị gộp 1 dòng (nghĩa không đổi). Kiểm 13 recipe gốc: giữ đủ comment, round-trip bằng nhau.
  (2) Lô không còn giới hạn 500 lượt: `GET /api/runs?batched=true|batch_id=` (json_extract `$meta.batch_id`, limit ≤2000);
  app nạp thêm lượt thuộc lô khi load list, BatchDetail nạp riêng theo batch_id.
  pytest 251/4 skip, typecheck sạch. CHƯA commit; Portable cần build lại core (PyInstaller) + app.
- [x] Commit + push `core+app: keep YAML comments…`. Build core (PyInstaller) + app. Tự test bản đóng gói (bản sao
  `scratchpad 81b71353/pkg` + data R6): PUT recipe sprite-8dir-3d-mv giữ 5 comment (ruamel có trong bản frozen), xoá lại
  OK; lô thử 3 lượt (recipe CPU `batch-test`) → `?batch_id`/`?batched` đúng, màn Hàng loạt + chi tiết lô hiển thị đúng.
  Portable: app đã chép 11:3x; core bị 12 studio-core.exe MCP khoá → `update-portable.ps1 -Wait` chạy nền. Nếu phiên đóng
  trước khi chép xong: tắt các phiên Claude/Codex rồi chạy
  `powershell -ExecutionPolicy Bypass -File L:\AI\AI-Asset-Studio\.planning\update-portable.ps1`.
- [ ] 2026-10-19 Animation (plan `.planning/PLAN-anim.md`, chủ nhân chọn cả 3, cho tự chạy GPU):
  A xong: `ops/spine.py` `animations()` 6 clip (idle, relax, walk, attack, hurt+nháy đỏ slot, jump), hướng chi tính từ
  pose gốc; anim_frames 8 khung. Live R6 (`scratchpad 81b71353/r6b`) 118s, problems [].
  C xong code: worker `animate_dirs` (khớp 2D mặt trước → 3D, armature 22 xương, bone heat → nếu fail thì skinning
  theo khoảng cách + smooth weight, walk procedural, render N khung × 8 hướng), executor `blender.animate_dirs`, recipe
  `sprite-8dir-walk`. Bone heat của Blender FAIL với mesh TRELLIS (kể cả proxy voxel / scale ×100) → dùng distance.
  Test nhánh trên mesh hiệp sĩ cũ (`scratchpad 81b71353/walk4`): 27s, sheet đi bộ 8×8 bước rõ.
  B: recipe `spine-character-mv` (Qwen mặt sau → nhánh back_* → export `swap_sides`), đang live (`r6mv`).
  App: art D 2 recipe mới, pitch, nhãn bước (front/joints/walk, back_* "· mặt sau"). pytest 253/4, typecheck sạch.
- [x] Animation xong cả 3 (CHƯA commit):
  B live `spine-character-mv` (`scratchpad 81b71353/r6mv`) 197s, 18/18 bước, 2 bộ Spine problems []; mặt sau
  `swap_sides` đúng (attack vung tay phải giải phẫu). App: khối Kết quả có nút "Mặt trước / Mặt sau" (và
  "Đứng yên / Đi bộ" cho recipe walk), nút Xuất theo bộ đang chọn; viewer Spine 6 anim OK (CDP).
  C live trọn `sprite-8dir-walk` (`scratchpad 81b71353/walkfull`, pháp sư chibi): 428s, anim 10.7s (distance
  weights), sheet đi bộ 8×8 đạt; viewer sprite phát đúng 8 hàng × 8 khung.
  Còn mở: miếng giáp vai Spine đứng yên khi hạ tay (lộ ở walk/hurt); hàng sheet đi bộ ghi "Hàng 1..8" chưa tên hướng;
  bone heat Blender không dùng được với mesh TRELLIS (đã thay bằng distance skinning).
  pytest 253/4, typecheck sạch.
- [x] Commit + push `core+app: spine 6 clips + back-view rig, sprite walk cycle…`. Build core + app; tự test core đóng
  gói chạy nhánh walk (27s, OK). Portable: app chép 15:12; core vẫn bị 11 studio-core.exe MCP khoá — vòng chờ
  `update-portable.ps1 -Wait` (nền, từ 11:3x) sẽ chép bản mới nhất khi hết khoá. CORE PORTABLE HIỆN CŨ → recipe
  sprite-8dir-walk / spine-character-mv chưa chạy được trên Portable cho tới khi chép core.
- [ ] 2026-10-27 (session mới, chủ nhân đi ngủ tới sáng; cho build + test thoải mái): tổng hợp + audit + brainstorm →
  `.planning/NIGHT-2026-10-27.md` (spec + plan + state), rồi làm theo plan. Audit core/app + inventory đang chạy subagent.
  Chủ nhân chốt cho đêm 2026-10-27: commit + push dần; được kill studio-core.exe MCP để chép Portable cuối đêm;
  Unity = project test riêng `L:\AI\unity-test` + exporter/importer, kiểm bằng `Unity.exe -batchmode`; chi phí API không giới hạn.
- [x] Audit 3 agent xong + Unity MCP v10.3.0 cài (project `L:\AI\unity-test`, compile sạch, đăng ký `unityMCP` user scope).
  Tổng hợp: `.planning/NIGHT-2026-10-27.md` (spec, audit C1–C15 / A1–A18, 10 ý tưởng, plan N1–N10). Bắt đầu N1.
  Chủ nhân (lần cuối trước khi ngủ): cho phép tự build + test lại khi cần; không còn câu hỏi treo. Giao wave 1: W1 core-fix,
  W2 app-fix + thư viện, W5 blender anim + normal, W6 exporter Unity/Godot, W7 pixel-art (xem NIGHT §4.1).
  W1: C2 op cache_material = blake3 của toàn bộ ops/*.py (spec bundle ops/*.py; Sheet ghép base|preset)
  W1: C3 claude.task/brief/translate timeout (_timeout_s mặc định 300, translate 90) → StepError claude_timeout retryable; /api/translate trả 504
  W1: C4 translate schema [{i,text}], gán theo index, bỏ entry thiếu/trùng/ngoài tầm
  W1: C5 runner: per-run control lock (_run_control) quanh load-state→mutate→start-driver ở resolve_gate/retry_step/cancel_run; test 2 resolve/2 retry đồng thời
  W1: C6 mesh_check: render lỗi → report/meta skipped=true, status/check="skipped" (passed=false; on_fail=fail không reject); free VRAM Comfy trước render
  W1: C7 engine.check_inputs() (coerce + blob tồn tại); POST /api/batches kiểm mọi item trước khi start run nào
  W1: C8 POST /resources/{name}/stop trả 409 resource_busy khi health=busy (override ?force=true)
  W1: C9 dọn proc khi cancel/lỗi lúc boot: blender _spawn (BaseException → kill, status down/error), comfy boot loop, _winproc assign lỗi → proc.kill()
  W7: op.pixel_art (ops/pixel.py: shared OKLab k-means palette, presets pico8/sweetie16/db16/db32/endesga32, palette_image, outline dark/hex, bayer, despeckle) + catalog + preset 'pixel' (field pixel → sheet meta filter=point) + recipes sprite-8dir-pixel / sprite-8dir-3d-pixel + tests/test_pixel_art.py (10 pass)
  W1: C10 ComfyClient.close() fail_all + _wait kiểm _closed → StepError comfy_stopped retryable (không còn internal)
- [x] W7 xong (review OK: `ops/pixel.py` op.pixel_art palette chung OKLab, 2 recipe pixel, preset `pixel`; ảnh
  `scratchpad 6ad407b8/w7/final_knight_auto16.png` đẹp). Chưa commit: `engine/kinds.py` lẫn hunk W1/W5 → commit gộp khi W1+W5 xong.
  W1: C11 library merge_text: bool/int không còn giữ nhầm giá trị cũ (so riêng bool, vẫn giữ format scalar ruamel)
  W6: export/unity.py (sheet .png.meta Sprite Multiple + nameFileIdTable, _NormalMap secondary, .anim/clip×dir, .controller blend tree 2D, prefab) — Unity 6000.3.6f1 batchmode L:\AI\unity-test-batch: 56 sprite, 16 clip, 2 state, 0 lỗi, Unity không ghi đè .meta
  W6: export/godot.py (SpriteFrames .tres AtlasTexture + CanvasTexture normal, .tscn AnimatedSprite2D offset chân, helper face()) — Godot 4.7.2 (L:\AI\tools\godot, SHA512 khớp) headless L:\AI\godot-test: 16 anim, 0 lỗi
  W1: C1 test giáp vai shoulder_l/r → upper_arm_l/r; C12 spine preview_html escape name (title/footer) + '</' trong JSON script
  W1: C13 index biểu thức idx_runs_batch_id (json_extract batch_id) trong db SCHEMA + test EXPLAIN
  W1: C15 op.sheet: anim.json không clips + angles dài = rows → meta.row_angles; sheet tĩnh có order/preset → meta.angles (per cell), cols=1 thêm row_angles
  W6: API POST /api/steps/{id}/export {engine: unity|godot, ppu?, pixel?} (GET trả engines; sheet tự ghép normal sheet anh em trong run), store/bundle.export_engine; app BundleMenu 'Xuất cho Unity…/Xuất cho Godot…' (+ preload/main/assetFile thêm tham số engine); tests/test_export_engines.py 7 pass; typecheck sạch
- [x] W1 xong (C1–C13, C15; 293 pass, 3 fail recipe-shape do W5 đang sửa). Interface mới: mesh_report `status/skipped`,
  translate 504 `claude_timeout`, stop resource 409 `resource_busy` (`?force=true`), sheet meta `row_angles`/`angles`. Đã báo W2/W5.
- [ ] W8 Project (core) bắt đầu: bảng projects + runs.project_id, apply_style, `api/projects.py`, MCP list_projects. App phần Project làm sau W2.
  W2: A1–A18 xong (BlobImage reset theo transport, stores loaded chỉ khi OK + Thử lại, gates/versions giữ event WS mới khi snapshot đang tải, ws watchdog resume 3s, fetch timeout 20s/180s, runs dọn preview/progress + LRU log, rev theo run, batch detail pool 6, GatesView loading/lỗi, focus trap Lightbox, guard dirty route + IPC quit, fuses electron-builder + will-redirect, fetchBlob LRU+timeout, dịch thiếu/504, notify cache-hit) + stop 409 xác nhận force; typecheck sạch
- [x] W6 xong: `core/studio_core/export/{source,sheet,unity,godot}.py`; `POST /api/steps/{id}/export` thêm `engine`
  (unity|godot), `ppu`, `pixel`; BundleMenu "Xuất cho Unity…/Godot…". Kiểm thật: Unity 6000.3.6f1 batchmode
  (`L:\AI\unity-test-batch`) fails=0; Godot 4.7.2 headless (`L:\AI\tools\godot`, `L:\AI\godot-test`) fails=0.
  Hạn chế: mới kiểm sheet giả lập; PPU chưa có UI; Spine chưa kiểm trong engine (không cài runtime).
  W5: N5 anim set — animate_dirs `anims` (idle/walk/run/attack procedural, attack = right-arm windup→strike→recover, one-shot), frames clip-major→dir→frame, anim.json `clips`+`angles`; op.sheet lays one 8-row block per clip (`clips` row0/rows/frames/fps/loop + row_angles). Live on walk4 knight: 4 clips×8f×8 dirs (+normals) 92 s.
  W5: N6 normal map — render_dirs/animate_dirs `normals: true` → `normal_frames` (camera-space RGB=n*0.5+0.5, alpha from colour frame); op.mirror_dirs `normal: true` flips R; op.sheet `normal: true`; `optional: true` on sheet/downscale/mirror passes empty input. Recipes 3d/3d-mv/3d-brief gain `normals` + normal_sheet/normal_meta.
  W5: C14 — animate_dirs raises blender_bad_output unless frames == Σclip frames × angles. New recipe sprite-8dir-anim (anims text "idle, walk, run, attack" + normals); sprite-8dir-walk unchanged. pytest 296 passed.
- [x] W5 xong (296 pass): anim idle/walk/run/attack (`blender.animate_dirs anims`), normal map (`normals`), recipe mới
  `sprite-8dir-anim`, `normals` cho 3d/3d-mv/3d-brief; ảnh `scratchpad 6ad407b8/w5/r1/` OK (cape theo tay khi attack do distance skinning).
- [ ] Audit wave 1 đang chạy: reviewer core (W1/W5/W7) + reviewer exporter/security (W6). Chờ W2 + W8 rồi commit theo nhóm.
  W8: core Project xong: bảng projects (+migrate runs/versions.project_id, index), contracts ProjectStyle/ProjectRow, engine/projects.apply_style (start_run khi project_id≠default, $meta.project_id/project_applied), api/projects.py CRUD+summary, filter project_id ở GET /api/runs + /api/versions, MCP list_projects + start_run(project_id); tests/test_w8_projects.py 7 pass (full 303 pass). App: bỏ input user chưa chạm để style áp được.
  W2: N4 Thư viện: chọn nhiều (checkbox, Shift dải, Ctrl/Cmd, Ctrl+A, Esc), thanh "Duyệt N / Từ chối N / So sánh / Bỏ chọn" (pool 4, tiến độ), A/R trong lightbox (duyệt rồi sang tiếp), CompareView 2 bản cạnh nhau; SpriteSheetViewer: tên hướng Việt + mũi tên + góc (row_angles / angles / fallback r*45), chip clip, nút Normal (normal_sheet / tên *normal*), phím ←/→ ↑/↓ Space N; MeshReport trạng thái skipped; mock walk sheet 3 clip + normal + mesh_report skipped; kiểm CDP mock OK; typecheck sạch
- [x] Commit + push `303dfbe` (core: W1+W5+W6+W7+W8, 303 pass) và `da978ac` (app: W2 + menu xuất W6, typecheck sạch).
- [ ] Wave 2: W9 app Project UI + PPU/IPC + chép export thay thư mục (M1/L4); W10 sửa audit exporter (H1 GUID trùng,
  M2 Godot 4.2, M3 heuristic walk, L1–L3, L5); audit core (W1/W5/W7) + audit app da978ac đang chạy.
  W9: recipe mới sprite-8dir-anim/-pixel/-3d-pixel có pitch + art (walk/glb/3d); nhãn bước op.pixel_art, animate_dirs 'Dựng xương + anim', ndirs/ndown/nsheet/anim_* '· normal map', anim_sheet
  W9: BundleMenu PPU (mặc định 100, nhớ localStorage) + Lọc pixel Tự động/Bật/Tắt → IPC saveBundle/revealBundle opts (main checkEngineOptions ppu∈(0,10000], pixel bool); xuất đè thư mục: hỏi 'Thay thư mục cũ?' rồi copy dest.tmp-<rand> → rm cũ → rename (hết lẫn .anim/.meta cũ)
  W10: exporter audit fixes — Unity GUID seeded by Source.uid=step.id (H1); Godot face() wrapf thay angle_difference (4.2 OK) + DIR_ANGLES sinh từ tracks, row_angles nhận góc lẻ/16 hướng (M2/L5); legacy-walk chỉ khi frames_per_dir hoặc lineage blender.animate_dirs (M3); normal sheet ghép theo key sheet→nsheet / n+key / key_normal hoặc cùng pivot lineage (duy nhất), so cả padding/width/height/order (L1); GLB trùng tên → name-2.glb (L2); Spine page giữ đúng tên trong .atlas (Hero.PNG) (L3). pytest 309 pass; Unity 6000.3.6f1 W6CHECK fails=0, Godot 4.7.2 W6GODOT fails=0.
- [x] Commit `8a601b8` (W10 exporter fixes) + `691e3b5` (normal sheet tên `sheet_normal.png/json`, 310 pass).
- [ ] W13 sửa audit core: blender.agent bị cắt 300s (HIGH), sheet_meta clip, pixel colors cap + perf, mesh_check status/free, worker normals engine switch, 409 starting.
  W13: ask() `_timeout_s` 0/None = không giới hạn (thiếu key = 300); blender.agent truyền timeout_s (900) vào ask → lỗi blender_agent_timeout; procedural code 600s/qa 300s, views judge 300s; sheet_meta trả None khi meta.clips hoặc frame None; pixel: outline dark auto chừa colors//4 slot (gộp dark bằng k-means trong budget) → palette ≤ colors; _nearest matmul chunk, kmeans bincount, despeckle vectorize (kết quả y hệt bản cũ, chỉ khác do budget), quantize LUT màu unique, executor tách shrink/palette/quantize từng frame qua to_thread + check cancel; catalog size max 512; 64×256→128 32 màu 3.46s→0.91s. mesh_check: passed trước skipped, chỉ /free khi nvidia-smi VRAM trống <3 GB (hoặc không đo được); ComfyResource.free bắt RuntimeError; worker render_shots: normals chạy pass 2 (Workbench đổi engine 1 lần/job) — Blender 5.2 render_dirs normals WORKBENCH+EEVEE OK; /stop 409 cả "starting". pytest 315 pass, 4 skipped. Chưa commit.
- [ ] Chờ W9 xong → W12 sửa audit app da978ac: H1 normal detect theo `meta.normal`/tên `_normal` + loại khỏi finalStep/featured,
  H2 BatchDetail pool sau unmount, M1 FrameBatcher flushNow + hidden fallback, M2/M3 Lightbox phím (menu lồng, A/R sau click viewer),
  M4 ws checkAuth timeout, LOW: client res.json timeout map, compareKeys reset, detect `_n$` regex, fetchBlob delete đúng promise,
  rendererDirty reset khi reload, runRevs dọn, setStatus revert đè WS.
  W11: R1 spine-character live (hiệp sĩ giáp vai) succeeded 166s, $0.068 (bones Claude), problems [], 15 slot/19 bone, 6 clip; giáp vai nằm trong part upper_arm (SAM không tách shoulder_l/r riêng → nhánh C1 không được kích hoạt) và theo cánh tay khi hạ/vung — OK. Ảnh: scratchpad 6ad407b8/w11/r1/frames_part*.png
  W9: Project app: types/client (projects CRUD+summary, listRuns/versions ?project_id), stores/projects.ts (active project nhớ localStorage, WS project.updated, summary debounce khi run/version đổi), mock 3 dự án + recipe sprite-8dir-3d-pixel + preset pixel; views Dự án (lưới thẻ cover/số động/lưu trữ/tạo nhanh) + chi tiết (sửa phong cách VI→EN, preset, pixel, ảnh palette/tham chiếu, đầu vào mặc định, ghi chú, lưu/hoàn tác/guard; tab lượt chạy + thư viện); sidebar Ctrl+8 + palette
  W9: Tạo mới/lô có chọn Dự án + chip 'Sẽ áp: …' (composables/projectStyle mirror apply_style), ô chưa chạm mà style phủ thì bỏ khỏi request (badge 'Theo dự án' / 'Dùng của dự án'), ảnh bắt buộc được style lấp; Run detail badge dự án + 'Đã áp từ dự án'; lọc dự án ở Lượt chạy + Thư viện (?project=); sửa ô text thừa dưới prompt (v-else chain). CDP mock OK sáng/tối (scratchpad w9/shots); typecheck sạch
- [x] Commit `c835c8f` (W13 core audit fixes, 315 pass) + `29cdd42` (W9 app Projects + PPU/IPC + thay thư mục export).
- [ ] Đang: W12 sửa audit app, audit W9 (29cdd42), W11 live GPU. Sau: sửa audit W9 → build core+app → kill studio-core.exe → chép Portable → smoke test → handoff.
  W11: R2 sprite-8dir-anim (core trước c835c8f) succeeded 442s, Claude $0.083 (views 0.057 + joints 0.026); views: 'left' hỏng 3 lần → dropped (mesh 3 view), mesh check passed iou 0.829; anim 4 clip×8 hướng×8f 78s; sheet 2048×8192 clips row0 0/8/16/24 + row_angles 32; normal sheet normal:true cùng layout. Texture mũ TRELLIS loang màu da (chất lượng mesh, không phải regression). Restart core theo c835c8f để chạy lại.
- [x] Audit 29cdd42: HIGH replaceDir có thể mất cả 2 bản trên Windows; M1–M5 (occupied nuốt lỗi, dialog Enter = xoá,
  form Project reset theo refetch, pixel null bị ghi default, refetch summary quá dày); L1–L5. Đã giao thêm cho W12.
  W11: R2b sprite-8dir-anim sau restart core c835c8f: succeeded 414s, Claude $0.118 (views 0.093 + joints 0.025); views 3/3 giữ, mesh passed iou 0.872 (165s); anim 77s; normal sheet đổi tên sheet_normal.png. Normal pass tách riêng (re-pose) → pixel normal sai (|n|<0.6 hoặc z<100) giảm 10.7% (R2 code cũ) → 0.98%; pose normal khớp màu. Không có cache hit (concept seed mới — đúng).
  W11: R3 sprite-8dir-pixel (GLB mesh R2b) succeeded 1.9s, $0: sheet 512×64, 14 màu (yêu cầu 16), alpha {0,255}, meta pixel/filter point/angles/dirs, pivot [32,53]; ảnh đẹp, viền tối 1px. Ảnh: scratchpad 6ad407b8/w11/r3/v_pixel.png
  W11: R4 project 'W11 Warm Kingdom' (suffix 'hand-painted, warm palette', preset pixel) + concept-2d project_id: succeeded 45s, $meta.project_applied=['prompt'], inputs.prompt có hậu tố, run.project_id đúng; preset không áp (concept-2d không có input preset — đúng).
  W11: R5 export (POST /api/steps/{id}/export ppu 64) anim_sheet R2b + sheet R3 → L:\AI\unity-test-batch\Assets\Live\{LiveKnight,LivePixel}, L:\AI\godot-test\Live\. Unity 6000.3.6f1 batchmode LiveKnight: 256 sprite ppu 64, _NormalMap secondary, 32 clip (attack loop=False 12fps), 4 state blend tree 8 con, prefab idle_S_0 → W6CHECK fails=0; LivePixel: 8 sprite filter Point, 8 clip idle, fails=1 chỉ vì checker đòi walk_S_0 (sheet tĩnh — đúng). Godot 4.7.2: LiveKnight 32 anim, CanvasTexture normal=true, offset -81 (pivot 209 khớp meta), face→walk_W, fails=0; LivePixel 8 anim, filter nearest, fails=0. Checker thêm env W6_DIR. Core/ComfyUI/Blender worker đã tắt sạch.
- [x] W11 live OK (core c835c8f, data `scratchpad 6ad407b8/w11/data`): spine-character 166s (giáp nằm trong upper_arm, C1 chỉ unit test);
  sprite-8dir-anim 414s, normal sai còn 0.98% (trước 10.7%); pixel 1.9s 14 màu; project áp suffix đúng.
  Export thật: Unity LiveKnight 256 sprite/32 clip fails=0; Godot 32 anim fails=0. Tổng ~$0.27.
- [ ] Build core PyInstaller (đang chạy), chờ W12 → build app → Portable.
  W12: H1 detect bỏ regex `_n$`, normal theo tên `_normal`/port normal_*, sheet chỉ ghép meta cùng bước, featuredAcross (Gallery gom theo step_id, ưu tiên sheet màu), RunDetail finalStep/bundle bỏ bước normal + ghép toggle sheet→nsheet / anim_sheet→anim_nsheet, bước normal tự xem được (viewer meta.normal → nút Normal khoá); mock walk dùng sheet/nsheet + tên sheet_normal.*. H2 BatchDetail pool bỏ id đã nhả + holder riêng mỗi instance. M1 FrameBatcher.flushNow + EventStream.flush, flush trước resync và đầu gates/versions load, fallback setTimeout khi hidden. M2/M3 Lightbox bỏ qua phím từ ngoài scrim/[role=menu]; data-own-keys chỉ giữ Arrow/Space/N. M4 checkAuth timeout 5s. LOW: client json timeout→ApiError timeout, listRuns lớn/batched dùng slow timeout, export engine 600s, compareKeys reset, fetchBlob xoá đúng promise, rendererDirty reset khi gone/navigate, versions setStatus revert theo seq + setStatusMany pool 4. (runRevs: không có đường xoá run → bỏ qua.) Audit 29cdd42: replaceDir đổi tên dest→.old rồi tmp→dest (retry EPERM/EBUSY, hoàn nguyên khi lỗi, không xoá tmp khi dest mất), destState chỉ ENOENT = trống, refuse khi là tệp, hộp thoại warning defaultId Huỷ + đường dẫn đầy đủ; ProjectDetail reset theo chữ ký (id/updated_at/style), pixel null = 'Không khoá', defaults id ổn định; projects summary map riêng, touch chỉ khi status run/version đổi, debounce 2s; active project archived → bỏ chọn, chặn chạy khi dự án chưa tải; touched bỏ giá trị = mặc định khi đổi dự án; ô style để trống = theo dự án (gợi ý + bỏ khỏi request); loadProject cache 60s; Run detail 'áp N' trong badge (tooltip đủ). CDP mock OK (scratchpad w12/shots); typecheck sạch. Chưa commit.
- [x] Commit `b82587a` (W12). Build core + app (lần 1 renderer cũ do `npx electron-vite` lỗi → build lại bằng
  `./node_modules/.bin/electron-vite`). Smoke test bản đóng gói trên data W11 OK. Kill 2 studio-core.exe Portable → chép Portable 02:56.
- [x] 03:00 Handoff mới: `.planning/HANDOFF-2026-10-27-0300.md`. Đang chạy: audit cuối core (8a601b8..c835c8f) + app (b82587a).
- [x] Audit cuối: app b82587a HIGH hero lấy nhầm frame normal (ndown) khi normals bật + MED normal ghép sai layout anim,
  NewRun khoá khi load project lỗi, debounce đói; core: không HIGH, MED timeout None/"" = vô hạn, palette trừ màu không cần,
  GUID Unity đổi theo run (ghi chú). Giao W14 (app) + W15 (core). Sau đó: commit → build → Portable → cập nhật handoff.
  W15: core xong — claude _timeout_s chỉ số 0 tắt timeout (None/""/thiếu → 300, brief chuyển nguyên giá trị); plan_palette auto chỉ nhường màu fill khi thiếu dark (giữ ≥2 fill); pixel size 8..512 → op_bad_params; 409 resource_busy có error.detail {resource,status}; render_shots trả lại filepath + normal_s; op.downscale param normal (tên `_down_n.png`, meta.normal) + ndown/anim_ndown trong 4 recipe; README Unity ghi chú GUID. pytest 319 passed, 4 skipped.
  W14: app xong — bước normal = params.normal / meta.normal / key cặp với bước màu cùng kind (ndirs/ndown/nsheet, anim_*, *_normal) / input port normal_* / mọi blob normal; tên frame normal chỉ `(^|_)dir_NNN(_…)?_n`; normal sheet chỉ ghép khi cùng width×height (detect + RunDetail + featuredAcross); hero anim có Đứng yên/Hoạt ảnh. NewRun chỉ khoá khi đang tải không lỗi, ProjectSelect viền lỗi + option "Thử lại" (retry khi đổi transport / mở select), project_id vẫn gửi khi list lỗi; touch() max-wait 5s; replaceDir lỗi khôi phục → báo đường dẫn .old/.tmp, xoá .old lỗi → toast info, backoff 100·2^i; Lightbox trap.onTab trước, chỉ bỏ qua [role=menu]/[data-popup]; FrameBatcher huỷ rAF/timer; Dashboard 409 theo detail.status (busy/starting). Mock: sprite-8dir-3d đủ dirs/ndirs/ndown, recipe sprite-8dir-anim (sheet tĩnh + anim + anim normal). CDP mock OK (scratchpad w14/shots); typecheck sạch. Chưa commit.
- [x] Commit `a6ca02c` (W15 core, 319 pass) + `6468699` (W14 app, typecheck sạch). Build core + app, smoke test
  bản đóng gói (anim run: Đứng yên/Hoạt ảnh, 8 hướng Việt) OK. Portable chép bản cuối 03:13. Handoff cập nhật 03:15.

## Phiên 2026-10-27 sáng (sau HANDOFF-2026-10-27-0300) — "làm tiếp": dọn mục còn mở
- [x] Pixel: outline `#hex` chiếm 1 slot trong `colors` (auto/image/preset), `plan_palette(stop=)` huỷ được giữa median-cut/k-means (`ops/pixel.py`); test `test_hex_outline_counts_toward_colors`, `test_palette_planning_can_stop`.
- [x] Cape theo tay: `adapters/blender/skin_math.py` (`distance_weights`, phạt xương tay cho đỉnh nằm sau tay >0.04h, x3); worker dùng nó, hash worker + spec PyInstaller có `skin_math.py`; `tests/test_skin_math.py`.
  Blender thật (mesh walk4, attack): weights=distance 19s; ảnh `scratchpad 6ad407b8/w16/compare.png` — mặt lưng hết cape dựng theo tay, mặt trước còn dải mép áo bên hông.
- [x] `uv run pytest -q` → 324 pass / 4 skip. Chưa commit, chưa build (chờ chủ nhân).
- [x] Commit+push `6d19023` (3 sửa mở). Chủ nhân: "làm cả 3" = commit/push + build/Portable + 4 ý tưởng.
- [~] Plan `.planning/PLAN-2026-10-27-ideas.md`; agent W17 (sprite_fx + QA score), W18 (texture-seamless), W19 (stats dashboard) chạy song song, không git.
- [x] W17 xong: `ops/fx.py` (op.sprite_fx shadow drop/blob + outline, normal → flat outline), bước `fx` trong 10 recipe sprite-8dir-*; `engine/qa.py` điểm QA lưu `versions.qa`, `api/qa.py` recompute, app badge/lọc/sắp xếp/lightbox. pytest 350 pass. Ảnh `scratchpad 6ad407b8/w17/fx_modes.png` OK.
- [x] W19 xong: `store/stats.py`, `api/stats.py` (/recipes /steps /daily /overview), MCP `recipe_stats`, app `CostStats.vue` trong Tổng quan. Khớp DB thật $0.2682/5 lượt. Ảnh `w19/shots/`. Lỗi nhỏ thấy: đường kẻ dòng bảng recipe đứt giữa cột.
- [~] W18 texture-seamless đang chạy.
- [x] Audit W19: H1 duration theo wall (retry/resume thổi phồng), M1 mất cost khi retry/fail, M2 KPI≠chart range, M3 kẻ bảng đứt + 9 LOW → giao lại W19 sửa.
- [x] Audit W17: không HIGH; M1 QA chạy trong lock trước SUCCEEDED, M2 recompute missing_only bỏ sót, M3 None đếm là updated, M4 grid dồn hướng + 6 LOW → giao lại W17 sửa.
- [x] W17 sửa hết 10 mục audit (QA nền sau SUCCEEDED, versions_to_score SQL, skipped:error, grid 1 hướng/ô, pixel shadow dither opaque…). pytest 358 pass.
- [x] W19 sửa audit (duration = hợp khoảng step, prior_cost_usd giữ chi phí retry/fail, since căn nửa đêm, bảng box-shadow, ...). pytest 358 pass. Chưa chụp lại (5173 bị W18 giữ).
- [x] W18 xong: `ops/tile.py` (seamless/tile_resize/tile_normal/tile_pixel/tile_check), recipe texture-seamless + tileset-seamless, export textures Unity (Repeat) / Godot (material + tiled scene), TileViewer. Live r4 stone 86, r5 grass 4 biến thể 97–100; Unity/Godot fails=0. Ảnh `scratchpad 6ad407b8/w18/`. Còn: sọc mờ trên texture phẳng (desert).
- [~] Audit W18 + audit tích hợp chéo (runner W17+W19, shared files, PyInstaller hiddenimports) đang chạy.
- [x] Audit tích hợp: ghép sạch, không HIGH; M1 cancel map mất cost; LOW: views judge cost, Lightbox QA cho ảnh đục, nút chấm điểm chớp, mock thiếu tile, MCP days, Gallery thiếu loại Texture.
- [x] Audit W18: H1 op.seamless nhân đôi nửa ảnh (nguyên nhân sọc/lặp desert) → đổi sang wrap-overlap quilting; H2 tile_check không bắt ảnh lặp; M1 tileset ghost; M2/M3 RAM; M4 normal sai biến thể; M5 méo ảnh không vuông + 8 LOW.
- [~] Giao sửa: W18 (H1–M5, LOW, mock tile, chạy live lại), W19 (cancel cost, judge cost, MCP days), W17 (Lightbox/Gallery QA, loại Texture, mockQa).
- [x] W17 sửa tích hợp: Lightbox ẩn QA ảnh đục/tile, nút chấm bỏ version <60s, mockQa skipped opaque, loại Texture ở Thư viện.
- [x] W19 sửa tích hợp: cancel/shutdown giữ cost item map đã xong (ctx.spent → prior_cost_usd), views judge + procedural cost, MCP recipe_stats căn nửa đêm. pytest 359 pass.
- [x] W18 sửa audit: quilting wrap-overlap + detrend, duplicate score, tileset quilt mép, RAM float32/pad, normal theo biến thể, viewer aspect. Live v4b desert/stone/grass 100, Unity/Godot fails=0.
- [x] pytest 362 pass / 4 skip; typecheck web+node exit 0. Commit `e941693` core + `cc8107d` app, pushed.
- [x] Build core + app, smoke bản đóng gói OK; Portable chép 08:15 (đã dừng studio-core MCP từ Portable).
- [x] 08:20 Handoff mới: `.planning/HANDOFF-2026-10-27-0820.md`

## Phiên 2026-10-27 08:3x (sau HANDOFF-0820) — "làm tiếp mấy mục còn mở"; chưa có quyền commit/build (hỏi lại khi xong)
- [~] W20 texture: grid period-aware, prompt chống tự lặp, Wang/corner tileset (+Godot terrain, Unity slice) → `w20/`.
- [~] W21 cape mặt trước (skin_math, kiểm Blender attack/walk/idle) → `w21/`.
- [~] W19b chi phí khi core bị kill cứng (lưu spent bền, recover gộp prior).
- [ ] QA hiệu chỉnh ngưỡng: cần thêm dữ liệu thật (để sau). Spine trong engine: chủ nhân chốt không cài runtime.
- [x] W19b xong: spent_live_usd ghi bền theo item/auto-retry, recover gộp prior; ctx.add_cost (max rule chống đếm trùng) cho claude/views/procedural/agent. pytest 369 pass.
- [x] W21 v1: skin_math geodesic arm distance + shell components (weld UV seams), cape mặt trước sạch, 45° còn mảnh nhỏ; phát hiện vertex_group_smooth headless chưa từng chạy. Ảnh w21/compare_*. [~] v2: smoothing numpy, shell cứng 1 xương, thử 45°.
- [x] W21 v2: smoothing numpy (smooth_body_weights, bỏ bpy smooth chết), shell cứng gắn 1 xương (khiên → hand_l), 3 xương/vertex. Stretch walk 53→23, attack 183→90. 45° mảnh cape dính tay: để lại. pytest 384 pass. Ảnh w21/v2_compare_*.
- [x] W20 xong: `op.seamless grid: auto|off` (detect_grid autocorr + keystone rectify_pitch + wrap_overlap khớp pha theo profile cạnh; ảnh tự nhiên không đổi), prompt Z-Image mới (bỏ "uniform pattern", thêm orthographic/no tile grid/non-repeating; cfg=1 nên negative vô tác dụng), `tile_check.self_repeat` (chỉ báo). Tileset `mode: edge|wang` + `op.tile_wang` (16 ô corner-Wang, atlas 4×4 + wang.json), Godot TileSet Match Corners + alternative tâm (headless 4.7.2: 0 góc lệch), Unity 16 sprite `wang_nn_xxxx` (batchmode fails=0), viewer Wang 5×5 + Xếp lại, chip vật liệu thêm dòng ở prompt nhiều dòng, `tile_resize square`. pytest 386 pass. Ảnh `w20/` (live_textures, live_wang_view, shots/). Còn: đá lát không đều/ván chu kỳ yếu không nhận lưới; gạch so le chỉ căn trục dọc; roll chưa nắn.
- [x] Audit W19b/W20/W21: H1 tay/tay áo rời bị coi shell cứng (khuỷu không gập); M geodesic chậm 300k=176s + NaN, xương ít đỉnh mất seed, checkerboard lệch nửa chu kỳ, wang 1 dòng fail sau GPU + phí gen, normal=false vẫn xuất wang normal; LOW viewer index, detect 32 ô, export prefix, add_cost tolerance.
- [~] Giao sửa: W21 (H1, M1, M2, L1, L2), W20 (M3–M5, L4–L6), W19b (L7).
- [x] W19b L7: lock per ctx cho add_cost, COST_EPS 1e-4 bỏ dư làm tròn. pytest 388.
- [x] W21 v3: shell cứng chỉ khi không quấn trục tay, geodesic frontier + cap 0.5h + guard NaN (308k verts 9.2s, knight 0.32s), xương ít đỉnh fallback plain, bincount, xương dài 0. Blender v3 trùng pixel v2.
- [x] W20 sửa audit: checkerboard NCC có dấu + fallback plain, tile_report BOX cỡ lẻ, wang chọn ảnh tốt nhất mỗi dòng + app chặn sớm, normal=false không xuất wang normal, viewer index/watch, wangSet 32 ô, export prefix `<asset>_wang*`.
- [x] Kiểm cuối: pytest 394 pass / 4 skip; vue-tsc web 0, tsc node 0. 25 file thay đổi, CHƯA commit/build (chờ chủ nhân).
- [x] Commit 609701c/ba65230/ff2c402 pushed; build + smoke OK; Portable 10:19. Handoff mới: `.planning/HANDOFF-2026-10-27-1020.md`
- [x] Xoá thư mục asset test: godot-test (Live, W6Knight, W18Grass, W18Stone, W20Meadow), unity-test-batch/Assets (+ Ref, .meta). Giữ script kiểm *_check.gd, Editor/*.cs.

## Phiên 2026-10-27 chiều (sau HANDOFF-1020) — "việc còn mở"; chưa có quyền commit/build
- [x] Recipe `rules:` (engine/recipe.py `_parse_rules`/`apply_rules`, chạy trong `coerce_inputs` → app/API/MCP đều qua):
  `min_items` (dòng × count ≥ N) + `max_lines` (cắt dòng thừa). tileset-seamless: Wang 1 dòng + count 1 bị từ chối trước GPU;
  edge chỉ giữ dòng 1, Wang giữ 2 dòng → không sinh ảnh thừa. App NewRun đổi câu gợi ý.
- [x] Gạch so le: `wrap_overlap` lọc đỉnh theo tương quan high-pass vuông góc (`CROSS_KEEP` 0.6) → trục y chọn số hàng chẵn
  (trước 2/6 ca lẻ, mạch đứng lệch nửa viên ở mép); `_worst_seam` dùng p99 (1024px running bond trước bị fallback plain).
  Test `test_running_bond_wraps_whole_bricks_and_an_even_course_count`, `test_tileset_rules_*`, `test_bad_rules_are_rejected`.
- [x] `uv run pytest -q` → 403 pass / 4 skip. Chưa commit, chưa build.
- [ ] Còn: cape 45° (cần Blender live), chu kỳ yếu + QA ngưỡng (cần ảnh thật).
- [x] Cape 45°: `skin_math` seed xương con (cẳng tay/bàn tay) phải tới được từ seed xương cha theo bề mặt, bỏ khi đường
  vòng > 2.5× đường thẳng (`DETOUR_RATIO`). Sim: cape-edge 0.66→0 trọng số tay, attack xé 290→152 cạnh, walk 124→8; khiên
  dính chung shell vẫn theo tay. Blender 5.2 thật A/B (attack/walk 8 hướng) hết vạt cape kéo lên ở 45°. 308k verts 10.1s.
  Commit `bb59623`. pytest 405 pass / 4 skip.
- [x] README.md mới + `docs/screenshots/` (dashboard, new-run, wang, gạch, thư viện, sprite hiệp sĩ, cape trước/sau).
- [x] Texture lưới trên ảnh thật W20 (17 ảnh Z-Image): gạch thật trước chỉ căn x (kiểm dấu độ sáng `signed>0` loại hết vì mỗi
  viên một tông) → ngưỡng `SIGNED_MIN` -0.5 (bàn cờ lệch pha ≈ -0.85) → 4/4 gạch căn cả x+y; lọc chẵn/lẻ chỉ khi tín hiệu ≥ 0.2.
  Thêm `_line_period` (chiếu profile cạnh/độ sáng): ván `live_planks01` nhận hàng 127px (trước mất 1 ranh ở mối nối → ván
  cao gấp rưỡi), đá lát 01 căn x. Chu kỳ < 3 lần/ảnh không căn (cỏ trước bị "lưới" 502px); `stamp_repeat` dùng `periodic`.
  tile_report giữ p95 (p99 làm ảnh thô qua ngưỡng 70). Commit `6c02fdc` + commit này. pytest 409 pass / 4 skip.
- [ ] Còn: đá lát nghiêng (keystone mạnh) chưa nhận; ngưỡng QA cần thêm run thật có duyệt/từ chối.
- [x] 14:23 Handoff mới: `.planning/HANDOFF-2026-10-27-1423.md`

## Phiên 2026-10-27 15:xx (sau HANDOFF-1423) — chủ nhân: "check progress rồi tiếp tục"
- [x] `npm run typecheck` (app) exit 0 — sau sửa `NewRunView.vue`.
- [x] Đá lát chụp nghiêng (`live_stone02`): `detect_grid` thêm `_strip_period` (3 dải 1/3 theo trục kia, projection độ sáng,
  mỗi dải ≥ `STRIP_LINE_MIN` 0.6, chu kỳ trôi đơn điệu, max/min ≤ `STRIP_MAX_DRIFT` 1.6); `_scale_profile` với entry
  `source: lines` đo chu kỳ từng band bằng `_line_near` (projection, `LINE_NEAR_MIN` 0.3) thay ACF 2D (hạt đá ~0.1).
  Kết quả thật: stone02 dải 298→332→370 → căn x + keystone pitch x 0.339, ron dọc thẳng khi lát (seam 89.3, y không
  đều nên chưa căn); stone01 thêm pitch x 0.285, lát sạch. 15 ảnh còn lại không đổi aligned/seam.
  Ảnh: scratchpad session `b257437f…/scratchpad/weak/tile_v6_stone0{1,2}.png`.
- [x] Test `test_pitched_flagstones_are_found_per_strip_and_rectified`; `uv run pytest -q` → 410 pass / 4 skip.
- [ ] Chưa commit (chờ chủ nhân). Còn mở: planks01 seam 73.8, ngưỡng QA cần dữ liệu duyệt thật.
- [x] Commit `d5ab4fd` (đá lát nghiêng + handoff 1423) theo lệnh "commit + làm tiếp".
- [x] Thử chấm mối nối planks01 bằng top-k dòng ron (k = 2·n/chu kỳ) thay p95: ảnh thô `live_planks01` y 1.46 (~88 điểm),
  `old_planks01` 0.44 → ảnh thô lọt ngưỡng → BỎ, giữ p95 (planks01 73.8 vẫn ≥ 70). Không đổi code.
- [ ] Backlog NIGHT §3 / PLAN-ideas đã xong hết. Còn duy nhất ngưỡng QA — cần chủ nhân duyệt/từ chối thêm lượt thật.
- [x] Build test theo lệnh chủ nhân ("build mới rồi mở lại"): `npm run dist:core` + `dist:portable` OK, tắt app/core Portable,
  `update-portable.ps1` (robocopy 3 = OK), mở lại app. 14 lượt QA vẫn `awaiting_human` (recover giữ gate).
- [x] 14 lượt QA (run title "QA tex 01..10", "QA sprite 01..04"): chủ nhân duyệt 10/10 texture. Phát hiện: Thư viện
  bỏ qua chấm mọi texture (`skipped: opaque`) → `engine/qa.py` `score_tile` (tile_report: seam/gradient/repetition/
  duplicate) cho version mà ref nằm trong output `op.seamless|op.tile_resize|op.tile_pixel` của lineage, trừ lineage có
  `op.tile_wang`; normal map vẫn bỏ qua. `QA_VERSION` 2 (recompute missing_only chấm lại bản cũ). 10 texture duyệt → 100
  điểm cả 10 (không oan); chưa có mẫu từ chối nên giữ ngưỡng 70. Test `test_seamless_texture_versions_are_scored_as_tiles`;
  pytest 411 pass / 4 skip. Chưa commit, Portable chưa có thay đổi QA này.
- [~] Sprite: cung thủ xong (QA 85–100), orc/rương/pháp sư đang mesh (GPU tuần tự). Chờ chủ nhân đánh dấu duyệt/từ chối
  trong Thư viện để so ngưỡng sprite.
- [x] 4 sprite QA xong (QA: cung thủ 85–100, orc 81–100, rương 98–100, pháp sư pixel 99–100). Chờ chủ nhân đánh dấu
  duyệt/từ chối trong Thư viện.
- [x] Cần duyệt chế độ "Tổng quan" (chủ nhân yêu cầu): `components/gate/GateOverview.vue` lưới mọi gate, tick chọn, ô
  ảnh bấm để chọn (đủ k thì tự tick), "Lấy ảnh đầu", "Chọn tất cả", Duyệt (N) gửi tuần tự pick/approve, Từ chối (M) chỉ
  gate duyệt; `GatesView.vue` nút chuyển Từng mục / Tổng quan (localStorage `gates-mode`). typecheck OK; kiểm bằng
  dev:mock + CDP (`scratchpad b257437f…/cdp.mjs`): 4 gate → chọn 3 → Duyệt → còn 1. Chưa commit, chưa build.
- [x] Giao diện kiểu macOS (plan `.planning/PLAN-ui-macos.md`): accent xanh hệ thống, control 24/28px chữ 400 nhấn tối
  (không co), `.card` = hộp nhóm phẳng, `.seg` dùng chung (gỡ bản chép ở 11 file), sidebar dòng 28px chia nhóm Làm việc /
  Kho / Công cụ, tiêu đề toolbar 15px, công tắc màu accent, thanh lọc Thư viện 26px. typecheck OK; ảnh trước/sau sáng+tối:
  scratchpad b257437f…/ui_before, ui_after. Chưa commit, chưa build.
- [x] 15:25 Handoff mới: `.planning/HANDOFF-2026-10-27-1525.md`

## Phiên 2026-10-27 15:4x (sau HANDOFF-1525) — chủ nhân: "commit xong đọc handoff + progress và tiếp tục"
- [x] Commit `16e0978` (core QA texture) + `97e2857` (app macOS UI + Tổng quan + plan/handoff). Đi trước origin 10 commit.
- [x] Ô chọn dự án màn Tạo mới: đo thật dev:mock → weight 400 (đã đúng sau sửa `ProjectSelect`), không cần sửa thêm.
- [x] Chủ nhân: "mặc định light theme" → `main/index.ts` `loadTheme` đặt `themeSource = 'light'` khi chưa có `ui.json`;
  `composables/theme.ts` ref mặc định `'light'`. Portable `userdata/` chưa có `ui.json` → bản build sau sẽ mở sáng.
  typecheck OK; dev:mock `prefers-color-scheme: dark` = false. Chưa commit, chưa build.
- [x] Commit `4940c9a` (light theme mặc định). Đi trước origin 11 commit.
- [ ] Ngưỡng QA sprite: DB Portable 112 version đều `draft` → chủ nhân chưa đánh dấu, chưa so được. Backlog NIGHT/ideas
  đã xong (xem dòng trên). Còn chờ: build Portable, push, đánh dấu sprite.

## Phiên 2026-10-27 16:xx — chủ nhân: "Spine R6 kìa làm đi" (đưa R6 ra khỏi R&D)
- [x] 7 lượt thật `spine-character` (driver `scratchpad b257437f…/r6/spine_live.py`, data `r6/data` code cũ: girl/wizard/
  goblin/rogue; `r6/data2` code mới: girl/wizard/knight), mỗi lượt 89–112 s, succeeded hết.
- [x] Lỗi thấy ở code cũ → sửa (`ops/parts.py`, `ops/rig.py`, `ops/spine.py`):
  1. Vá torso phình ra ngoài dáng ngực (giáp vai thành 2 "tai" xanh khi hạ tay) → `joint_fills` kẹp torso trong
     `hull_mask`, chi kẹp trong ống quanh xương (`UNDER_WIDTH_SLACK` 1.2). Hiệp sĩ cũ: vùng vá torso 18152 → 4789 px.
  2. Tóc dài / áo choàng sau tay T-pose rách ngang khi hạ tay → `behind_arms` (cột có cùng lớp head/cape/torso ở trên
     và dưới đoạn tay, nối khe ≤ `BEHIND_GAP` 32 px) đưa vào vùng vá; `inpaint` thêm head, cape (2 recipe + default).
  3. Mảnh vụn không lõi (cape pháp sư 1910/6578 px) trôi lơ lửng → `absorb_specks` gán sang lớp kề.
  4. Tay áo rộng xoay thành "cánh" → `arm_drop` (cẳng tay dày > 3× bàn tay ⇒ hạ tay ×3/r, tối thiểu 0.4); pháp sư 0.52.
  Ảnh: `r6/data2/out/*/view_*.png`, `r6/wiz2_re0.png`, `r6/cmp_holes.png`.
- [x] Bỏ nhãn "(R&D)" / "(thử nghiệm)" ở 2 recipe Spine + art index + mock. Test mới 4 (`test_p5_rig.py`);
  pytest 415 pass / 4 skip; typecheck OK. Chưa commit.
- [ ] Còn nhỏ: mép tóc có bậc vuông ở ngang vai; vệt nối đầu gối khi chân nhích (walk); đầu nghiêng tách tóc trước/sau.
- [x] Commit `af50377` (spine fill/specks/sleeves, bỏ R&D) theo lệnh "commit rồi làm tiếp".
- [x] Lượt thật `spine-character-mv` (`r6/data3`, mvgirl) 174 s succeeded, 2 bộ problems [], mặt sau iou 0.999 → không hồi quy.
- [x] Đầu nghiêng làm tóc dài văng/tách (khung hurt) → `spine.head_swing` (cao lớp head / dài head_top→neck > 1.6 ⇒ xoay
  đầu ×1.6/r, tối thiểu 0.25); girl 0.6. Ảnh so: `r6/hurt_cmp.png`.
- [x] Khớp chân lệch: `merge_joints` nhận từng khớp riêng → hông trái Claude (y 598), hông phải auto (699). Vẽ so
  (`r6/knees.png`): gối Claude đúng (giữa giáp gối / chân lộ, ~0.35 hông→cổ chân), gối auto (0.5) quá thấp.
  `_keep_limbs_sane`: hông/vai cùng độ cao (lệch nguồn ⇒ theo bên Claude; cùng nguồn lệch > tol/2 ⇒ trung bình);
  khớp giữa ngoài 20–80% chiều dài chi ⇒ cả chi về auto. 9 mẫu: chỉ pháp sư (áo che chân) dùng auto hông/gối.
  Test +2; pytest 417 pass / 4 skip. Chưa commit.
- [x] Mép tóc bậc vuông ngang vai → `behind_arms` vá cả cột chỉ thấy tóc 1 phía nhưng thuôn dần (`_taper`: sâu ≤
  1 − khoảng cách/độ dài đoạn) → mép chéo nối mép tóc trên/dưới. Test +1.
- [x] Lượt thật `r6/data4` (girl 124 s, wizard 101 s): pháp sư hạ tay gọn; girl lộ lỗi cũ: SAM gộp ủng trái vào mask
  `arm` → auto chuỗi tay trái kéo xuống ủng, Claude bị loại (xa auto > 4×tol), ủng bay theo tay. Sửa
  `_drop_stray_arms` (pixel tay dưới tâm thân không nối phần tay trên → bỏ nhãn cho lớp kề nhận): arm_l 21761→9149,
  leg_l 9357→21969, mọi khớp claude; dựng lại offline (`r6/full_re.py`, ảnh `r6/girl4_re_v.png`) đúng. Test +1.
  pytest 419 pass / 4 skip. Chưa commit.
- [x] Commit + push `core: spine heads tilt less…` (master = origin).
- [x] Lượt thật `r6/data5` (samurai/dwarf/witch, 95–105 s): dwarf, witch (áo choàng sau tay vá liền) đạt; samurai hỏng —
  SAM gán tay áo kimono vào `cape`, tay chỉ là cẳng tay trần → tay áo đứng yên khi hạ tay. Sửa `_claim_sleeves`: pixel
  torso/cape ngoài bề ngang torso (ở hàng của tay), chạm tay, đáy trên eo (đáy torso) → nhập vào tay; áo choàng dài quá
  eo giữ `cape`. Dựng lại 7 nhân vật: samurai tay 8860→36018 px, witch/knight/rogue không đổi. Test +2 (tham số).
- [x] Lượt thật `r6/data6` (samurai 104 s, girl 102 s) với code mới: samurai tay áo theo tay, girl ủng đúng chỗ.
  Ảnh `r6/v6_*.png`. pytest 421 pass / 4 skip. Chưa commit.
- [x] Commit + push `core: spine rigs take wide sleeves…`.
- [x] Lượt thật `r6/data7` (princess/catgirl/barbarian, 95–105 s): catgirl ổn (đuôi theo đúng; dây áo đứt nhẹ ở attack).
  Princess: váy dài bị SAM chia vào 2 chân → khung walk xé váy dọc. Sửa `spine.leg_stride` (lớp đùi+cẳng chân trái/phải
  giáp nhau > 40% chiều cao chân ⇒ walk/jump không nhấc chân, chỉ nhún). 11 nhân vật: chỉ princess = 0. Dựng lại
  offline (`r6/re_princess_v.png`) hết toác. Test +1; pytest 422 pass / 4 skip. Chưa commit.
- [ ] Giới hạn đã biết (chưa sửa): khăn lông vai barbarian gán vào upper_arm, khớp vai nằm giữa khăn → phần trong vai
  dựng lên khi hạ tay. Tách sang torso sẽ xẻ đôi giáp vai kim loại (hiệp sĩ) → để nguyên.

## Phiên 2026-10-27 19:xx — chủ nhân: "check handoff + progress, tiếp tục, tự build lên test"
- [x] pytest 422 pass / 4 skip. `npm run dist:core` + `dist:portable` OK; tắt process Portable, `update-portable.ps1`
  (19:06), mở lại app. Portable giờ có leg_stride (chưa commit).
- [x] Test bản build: 3 lượt `spine-character` chạy bằng `resources/core/studio-core.exe` của Portable
  (driver `scratchpad edad5c82…/r7/portable_live.py`, data `r7/data`): princess / druid / pirate.
- [x] Kết quả lượt Portable `r7/data`: princess (stride 0, váy liền), druid (áo choàng liền) đạt, problems [] cả 3.
  Pirate hỏng: SAM gộp vạt áo khoác dài vào mask `arm` → vai auto rơi xuống vạt (y 648), vai Claude (y 290) bị loại vì
  xa auto > 4×tol → hạ tay thì vạt áo xoè như cánh. Sửa `parts._shed_coat_tails` (sau `_claim_sleeves`): pixel tay
  dưới phần vươn xa của tay (≥ 0.6 tầm với), chạm torso/cape/chân, đáy quá tâm thân → nhãn `cape` (không có → torso).
  Dựng lại 21 mẫu cũ: chỉ pirate/goblin/catgirl đổi, cả 3 xem ảnh đều ổn (`r7/re_*_s.png`). Test +2 (tham số);
  pytest 424 pass / 4 skip. Build Portable lại 19:17. Chưa commit.
- [x] Lượt Portable `r7/data2` (bản 19:17, 100–109 s, problems [], mọi khớp claude): pirate hạ tay gọn, vạt áo đứng
  yên; vampire (áo đuôi tôm) đạt. Detective: concept không T-pose (tay phải cầm kính lúp lên mặt) → vai phải lòi một
  mảng áo nhọn khi hạ tay. Lỗi do prompt bắt tay cầm đồ trước mặt, chưa sửa (giới hạn đã biết). Ảnh `r7/v2_*.png`.
- [x] (đã commit `bd00a14`) `core: spine walks keep gowns whole; coat skirts in the arm mask go to the cape`
  (`ops/spine.py`, `ops/parts.py`, `tests/test_p5_rig.py`, `.planning/PROGRESS.md`).
- [x] Commit `bd00a14` (gown stride + coat tails) theo lệnh chủ nhân.
- [x] Detective: cả 8 concept của 2 lượt đều vẽ thêm "tay thứ 3" cầm kính lúp (lỗi của Z-image khi prompt có "holding X"
  đi cùng T-pose). Chọn tay ảnh 2 (kính lúp trên mặt, tay sạch) thì rig ổn, trừ đai cổ tay áo phải bị SAM gán `torso`
  → đứng yên lơ lửng. Sửa `_claim_sleeves`: bề ngang torso chỉ tính các cột torso cao ≥ 25% cột cao nhất
  (`TORSO_CORE_COL`). So HEAD↔mới trên 25 lượt (`r7/cmp_head.py`): đổi ở detective/pirate/samurai…, dựng lại xem ổn.
  Test `test_cuff_strap_segmented_as_torso_joins_the_arm` (fail khi chưa sửa).
- [x] Archer (cung chắn ngang thân): cung vỡ làm đôi (phần theo tay, phần đứng yên). Thêm vai `prop`: SAM prompt thứ 7
  "weapon:2, bow, staff, sword, shield, wand" (chỉ `spine-character`, chưa sửa `-mv`); `ROLE_PRIORITY` prop sau arm;
  `parts._attach_props`: tràn từ chỗ prop chạm tay → `prop_l`/`prop_r`, phần không chạm tay trả lại lớp kề;
  `spine.PART_BONE` prop_* → hand_*; vẽ sau cùng (ngoài DRAW_ORDER). Test +1. pytest 426 pass / 4 skip.
  Build Portable 20:48. Chưa commit.
- [x] Lượt `r7/data4` (bản 20:48) lộ lỗi prompt prop: "staff" khớp cả người (nghĩa nhân sự); nhân vật không vũ khí
  SAM trả mask cả người (girl fail `parts_missing`); "shield" khớp giáp ngực knight. Thử SAM trực tiếp
  (`r7/samtry.py`, ComfyUI 8188): prompt mới "weapon:2, bow, arrow, sword, axe, spear, wand"; `PROP_MAX_OF_FIGURE`
  0.35 (mask prop > 35% hình → bỏ); prop ưu tiên thấp nhất (sau cape) → pixel trong mask thân giữ cho thân. Dựng lại
  offline (`r7/re_prop.py`): archer cung theo tay phải (mũi tên chia 2 nửa theo 2 tay), knight kiếm theo tay, giáp
  liền. Test +1; pytest 427 pass / 4 skip. Build Portable 20:57.
- [x] Lượt Portable `r7/data5` (bản 20:57, 106–117 s, problems [] cả 3): knight kiếm `prop_r` theo tay, giáp liền;
  girl không vũ khí đúng như cũ (mask cả người bị bỏ); druid gậy vắt trước áo đi liền theo tay. Ảnh `r7/v_data5_*.png`.
- [x] Commit `9763a9c` (prop + đai cổ tay) theo "ok cứ tiếp tục".
- [x] `spine-character-mv`: thêm prompt/role prop cho cả segment trước và sau (2 skeleton độc lập, không ghép tên).
- [x] "Tay thứ 3": so prompt concept bằng `r7/concepts.py` (core dev, dừng ở pick, huỷ): "holding nothing" (A) →
  "exactly two arms, any held item gripped in the hand at the end of a straight outstretched arm" (B). Detective A 4/4
  mọc tay → B 3/4 sạch; knight B 4/4 cầm kiếm; mage B 4/4 cầm đũa; girl/princess B tay không, không thêm đồ. Áp B cho
  cả 2 recipe (`r7/cdata/AB_*.png`, `B_plain.png`).
- [x] Lượt thật mv knight (core dev, `r7/data6`, 179 s): trước `prop_l` 8802, sau `prop_r` 8859, problems [] cả 2.
  Attack vung tay không cầm kiếm → `spine.lead_hand` (bên có prop lớn hơn, mặc định r) + `animations(lead=…)`.
  Dựng lại offline: attack giơ tay cầm kiếm. Test +1; pytest 428 pass / 4 skip. Build Portable 21:29. Chưa commit.
- [x] Vệt nối đầu gối khi walk: soi khung walk knight `r7/data5` (cắt cận) → liền, không thấy vệt (walk xoay cả chân
  như một khối). Coi như hết.
- [x] Weighted mesh vai (chủ nhân: "sửa luôn đi", plan `.planning/PLAN-spine-weighted-mesh.md`): `spine.arm_mesh` —
  upper_arm_* thành mesh lưới (ô ~min(w,h)/6, 8–24 px), trọng số torso↔upper_arm theo t dọc vai→khuỷu (smoothstep
  `ARM_BLEND` −0.05→0.35); `compose` vẽ mesh (`mesh_points` + raster tam giác barycentric, bilinear);
  `validate_skeleton` kiểm mesh (`_mesh_problems`). Đối chiếu runtime thật `@esotericsoftware/spine-core@4.2.43`
  (`scratchpad edad5c82…/spinecheck/check.mjs`): đỉnh mesh barbarian setup/relax/attack lệch ≤ 0.0001 px.
  Dựng lại barbarian (hết "tai" lông), knight (mép giáp vai liền, hết gai), girl, samurai ổn.
- [x] Lượt thật Portable 22:33 `r7/data7`: barbarian khăn lông nằm yên trên vai. Viking lộ: SAM "weapon" bắt dây đeo
  chéo ngực, chạm vai trái → `prop_l` kéo theo tay, thủng ngực. Sửa `_attach_props`: chỉ gieo prop ở chỗ chạm phần
  ngoài tay (tầm với ≥ 0.6 · tầm với tay, như bàn tay). Test +2 (mesh, dây vai — fail khi chưa sửa).
  pytest 430 pass / 4 skip. Build Portable 22:41; lượt thật `r7/data8` viking (rìu theo tay, ngực liền), archer OK.
- [ ] Chờ chủ nhân: commit `core: spine upper arms are weighted meshes; attacks swing the weapon hand; concepts keep
  held items in the outstretched hand`.
- Giới hạn còn: SAM "head, hair" có khi ôm cả áo choàng lông (viking `r7/data7`) → áo choàng xoay theo đầu (nhẹ);
  mũi tên vắt 2 tay: nửa phía tay trái đi theo tay trái.

- 23:43 commit `7e29602` (weighted mesh vai, lead_hand, prompt concept, prop -mv) + push origin/master. Build Portable 23:43, đã mở.

- 23:5x README viết lại cho người dùng (mô tả + cài đặt + dùng lần đầu + lỗi hay gặp), ảnh UI mới chụp qua CDP (`docs/screenshots/`: dashboard, new-run, library, run-sprite, settings-tools, spine-frames). Thêm `setup/comfyui-models.ps1` (-Root) + `setup/workflows/` (chép từ L:\AI\setup). Plan cài một chạm: `.planning/PLAN-one-click-setup.md` (chưa làm).

## Phiên 2026-10-28 00:xx — Cài tự động (one-click setup)

- [x] `core/studio_core/bootstrap/`: `manifest.json` (uv 0.11.21, ComfyUI 0.41.0 zip, torch 2.14.1/cu130, Blender 5.2.2
  zip + sha, Claude installer, 4 gói model + 20 file HF có size/sha256 lấy từ header `x-linked-*`), `plan.py` (gói →
  model suy từ workflow, GPU/driver ≥ 580, ổ trống, `recipe_packs`), `job.py` (thread nền; tải Range resume + sha256,
  2 luồng/gói; uv venv `only-managed` + torch + requirements; Blender zip; Claude `irm|iex`; lưu settings).
- [x] Workflow chuyển vào `core/studio_core/workflows/` (đóng gói theo PyInstaller); `comfy_workflows_dir` mặc định trỏ vào đó.
  `setup/comfyui-models.ps1` thêm `pixal3d_multiview` (thiếu trước đây).
- [x] API `/api/setup` (plan), `/job`, `/start`, `/cancel`, `/claude-login`. App: Cài đặt → **Cài tự động**
  (`SetupPanel.vue`), GettingStarted bước 1 → "Cài tự động", nút ở Đường dẫn công cụ. Mỗi gói một dòng tiến trình
  (dung lượng, %, file đang tải). Mock có setup giả.
- [x] Test `tests/test_setup.py` (manifest đủ model, resume, sha sai xoá, job end-to-end với HTTP server giả, API).
  pytest 436 pass / 4 skip, typecheck sạch. Portable build 00:13.
- [~] Kiểm thật: tải ComfyUI zip + uv + venv + torch chạy được (lượt 1). Lượt chạy lại đầy đủ (venv managed, Blender,
  model thật, ComfyUI boot) bị auto-mode chặn vì chạy code tải ngoài → chờ chủ nhân cho phép / tự bấm trong app.
  Rác thử nghiệm: `L:\AI\setup-test` (xoá được).
- [x] 00:3x v1.0.0: bump version (app/core), build bộ cài + zip portable (smoke test zip OK: core kết nối, data trong thư mục portable), README thêm mục Tải về.

## Phiên 2026-10-28 10:xx — Thêm anim cho skeleton Spine có sẵn (yêu cầu session spine-test, R1–R5)

Plan: `.planning/PLAN-spine-anim-import.md`. Yêu cầu gốc: `L:\AI\project\spine-test\asset-studio\REQUEST-spine-anim-import.md`.
- [x] R1 input `file` (`ext`, `siblings: atlas_pages` → atlas + trang png thành 1 list blob) và `video`; mime mp4/webm;
  MCP tự gom trang png theo atlas (`spine_atlas.py`), kiểm đuôi file; runner kiểm `ext` theo tên blob.
- [x] Runtime Python `ops/spine_runtime.py` (bone timeline bezier, IK 2 bone, skin ghép, region/mesh, atlas xoay/offsets,
  PMA). Đối chiếu spine-core 4.2.43: lệch đỉnh ≤ 0.02 (setup/idle); chưa hỗ trợ deform/attachment timeline.
- [x] Op `op.video_frames` (ffmpeg, setting `ffmpeg_exe`), `op.spine_rig_summary`, `op.spine_add_anim` (port gen_sway,
  chèn anim vào text JSON giữ nguyên byte phần còn lại, kiểm bone + frame đầu/cuối = setup), `op.spine_render_strip`.
- [x] `claude.spine_review_loop` (render → Opus chấm/sửa spec, đưa critique vòng trước vào prompt, giữ vòng điểm cao nhất,
  dừng ở `accept_score`); Codex second opinion chưa làm (log cảnh báo).
- [x] Recipe `spine-anim-ref`; `claude.task` nhận `schema: motion_spec|motion_review`, meta có `score/critique`.
- [x] R5: MCP `pending_gates[].review` (score, critique, score_history), `steps[].score`; app `ScoreBadge`,
  `ReviewDialog` (critique Markdown, strip vòng ↔ sheet tham chiếu, diff spec, tải JSON, chọn vòng), badge ở gate + run row.
  App: `BlobDropZone` nhận file/video/atlas+trang (chọn cùng lúc), video xem trước.
- [x] Test `tests/test_spine_anim_ref.py` (14). pytest 450 pass / 4 skip; typecheck sạch.
- [x] R4 thật qua lớp MCP (StudioTools + core dev, scratchpad `608ff0e6…/scratchpad/r4/`): điểm 4 → 6.5 → 7.2 (3 vòng,
  chưa tới 8); spine-core load OK, t=0 và t=3.6 lệch setup 0; bỏ entry `sway` thì file == gốc từng byte.
- Giới hạn: auto-crop video không cắt được khi nền là UI game rối; badge chưa có ở danh sách version; chưa build Portable.
- Chưa commit.
- 11:15 commit `ba302df` (R1–R5, chủ nhân duyệt). Tắt core Portable, build core + Portable, chép vào `L:\AI\AI Asset Studio Portable`; MCP Portable list_recipes thấy `spine-anim-ref` (22 recipe). `update-portable.ps1` trỏ sang release\1.0.0.

## R6 — giới hạn gập khớp (yêu cầu spine-test, run ae551415 bị chê lộ khuỷu)
- [x] `spine_anim`: `_tip_joints` (khớp ở đầu bone cha; cứng = không có mesh weighted trải 2 bone), `joint_limits`
  (hard seam 20°, spec `elbow_max`/`joint_limits` chỉ được siết), `bend_range` (tổng gập ≤ limit, duỗi ngược ≤ 3°;
  khớp vẽ sẵn gập quá limit thì ±limit từ setup), kẹp `arm_keys`, port `arm_lag`, `deepest_bend`; rig summary ghi khớp.
- [x] Review loop: ảnh zoom khớp lúc gập sâu nhất + mục chấm mối nối + danh sách giới hạn trong prompt; review.json có
  `zoom`, `deepest_bend`; app ReviewDialog hiện ảnh zoom. Prompt bước spec nêu giới hạn.
- [x] Test +3 (17 trong file). pytest 453 pass / 4 skip; typecheck sạch.
- [x] Chạy lại input run ae551415 (6 vòng, accept 8) qua MCP core dev (scratchpad `608ff0e6…/r6`): 6 → 7.2 → 7.6 → 8.0;
  khuỷu tổng gập max 10.9° (L −10.9…−2.4, R 2.8…10.9); critique cả 4 vòng nhắc giới hạn khớp; spine-core load OK,
  đầu/cuối = setup.
- Chưa commit, chưa build Portable.

## R7 — khớp được che, nhún hông, kiểm gối đè (yêu cầu spine-test, góp ý run 9aabb037)
- [x] `spine_anim`: `joint_covers` (auto: skin render có attachment trên chi đó, vẽ trên mọi attachment skin khác của
  2 bone, phủ đĩa bán kính 10% bone con quanh khớp ở setup; `none`; danh sách bone) → hard seam được che 40°
  (spec chỉ siết); `leg_chains`/`hip_bone` (tổ tiên chung 2 chuỗi IK chân); `bounce` → `translatey` riêng cho hip
  (+15% giữa beat, về 0 sau beat cuối 0.5s, bỏ mốc depth 0), `translatex` tách; `knee_gap` (mẫu 0.02s, <35% hoặc
  chéo = overlap); rig summary ghi khớp được che + dòng "Hip for bounce".
- [x] Op/recipe: input `joint_cover` (auto); rig_summary/add_anim/review nhận `skin` + `joint_cover`; prompt spec dặn
  cong khuỷu khi được che, đếm số lần nhún cả clip, shift_x vừa phải.
- [x] Review loop: zoom 2 gối lúc gần nhất, rubric tay chân không đè, gợi ý giảm shift_x/bounce; round có overlap
  không được chọn/accept; review.json `cover`, `warning`, `knee_overlap`, mỗi vòng `knee`, `knee_zoom`, `bounce`;
  critique gate có dòng "Lưu ý" phải mặc trang phục che. MCP pending gate có `warning`, `knee_overlap`.
- [x] App ReviewDialog: banner cảnh báo che khớp, ảnh zoom gối + khoảng cách, dòng nhún hông.
- [x] Test +2 (19 trong file). pytest 455 pass / 4 skip; typecheck sạch.
- [x] Nghiệm thu (scratchpad `608ff0e6…/r7`, input 9aabb037 + outerwear_7 + 3 góp ý):
  auto run 47484af7: 6 → 7.5 → 8.2; khuỷu tổng max 38.4/38.8°, cổ tay offset −10…+11; hông hạ 9px đúng 2 lần
  0.9/1.86s trùng biên translatex 0.9/1.84; gối gần nhất 52% (không overlap).
  none run 8376bd34: 5.5 → … → 8.0; khuỷu ≤10.8° (giữ 20°); 2 lần hạ 0.9/1.9; gối 63%.
  Cả 2: spine-core load OK, đầu/cuối = setup, bỏ entry sway = nguyên byte gốc.
- Lần chạy đầu fail vì Claude ghi bounce kèm mốc [0,0]/[3.2,0] → generator bỏ mốc depth 0; Claude từng hiểu "2 lần"
  là mỗi chu kỳ (5 lần) → prompt ghi rõ tổng cả clip.
- Chưa commit, chưa build Portable.

## R8 — hàn khớp bằng mesh weight 2 bone (luật: skin/trang phục không được lộ khớp)
- [x] `ops/spine_weld.py` (port tools/weld_joints.mjs bản mới): khớp auto = tip joint hard seam + con của chuỗi IK 2
  bone (gối); luật theo từng mảnh: mảnh chỉ bám 1 bên khớp mà skin có mảnh bám bên kia → region thành mesh lưới
  2×(N+1) (hull = mọi đỉnh) / mesh 1 bone thành weighted / mesh weighted chia lại phần cha-con; mảnh đã bám cả 2 bone
  giữ weight artist; weight con = smoothstep(−r, r) dọc bisector, r = 25% bone ngắn hơn (≥8); mảnh có deform bỏ qua
  + cảnh báo. Chỉ viết lại member `skins`, mọi byte khác giữ nguyên.
- [x] Op `op.spine_weld_joints` (skeleton, spec tuỳ chọn → skeleton, original, joints); recipe chạy weld trước rig;
  rig/add_anim/review nhận `welded`; khớp đã hàn giới hạn tự nhiên 40° (spec chỉ siết, bỏ bone chuỗi IK).
- [x] Review: zoom khớp 2 lần (skin_parts + chỉ da `bare_skin`, mặc định skin đầu), schema `seam_visible`, vòng lộ mối
  nối không được chọn/accept; cảnh báo gate "rig đã đổi attachment quanh khớp … thành mesh weighted"; modal hiện zoom
  chỉ da. MCP pending gate có `seam_visible`.
- [x] So bản JS: 6 khớp, 183 attachment cùng tập; ở tư thế gập, 103 mảnh so được trùng 0.00px. Khác ở region bị
  trim: JS lấy góc vùng trim trong khi uv mesh Spine phủ kích thước gốc → bản JS kéo giãn ảnh (tới 44px); bản Python
  đúng (render setup lệch ≤2/255).
- [x] idle/walk: ngoài vùng trộn lệch 0px; trong vùng trộn khác (đó là chỗ được uốn liền, cố ý).
- [x] Test +5 (24 trong file). pytest 460 pass / 4 skip; typecheck sạch.
- [x] Nghiệm thu (scratchpad `608ff0e6…/r8`): không outerwear (6cd09184) 6 → 7 → 7.5 → 8; có outerwear_7 (fe501e6d)
  7 → 8. Khuỷu tổng 38.4/38.8° và 36.4/36.8°; cổ tay tới 40° liền mảnh ở cả zoom có áo và chỉ da; seam_visible false
  mọi vòng; hạ hông 2 lần; gối 42%/55%; spine-core load OK, đầu/cuối = setup.
- [x] R8 bổ sung 2+3 (port weld_joints.mjs bản mới): khớp bản lề trộn toàn bề ngang chi trong |s|<2r; khớp ổ (vai,
  háng: bone cha không chiều dài) chỉ chỉnh mảnh bám bone con; "phía cha" = cha + tổ tiên tới bone có chiều dài + con
  cháu gần khớp không phải chuỗi ≥3 đoạn + bone thân dẫn tới khớp khác; mảnh bám 2 phía nhưng weight bật/tắt vẫn
  hàn; mesh quad không weight chia lưới lại; háng có háng anh em sát bên → "wrap": đỉnh mảnh thân nằm trong đa giác
  mảnh chi cùng skin trộn theo chi, region thân chia lưới 2 chiều ~30px. Auto = 10 khớp (vai, khuỷu, cổ tay, háng,
  gối ×2), 230 mảnh; so bản JS cùng tập, cùng cấu trúc, lệch ≤0.007px (làm tròn). Khớp ổ/IK không đặt giới hạn.
- [x] R9: `held_bones` (bone không chiều dài dưới bàn tay, slot chỉ có skin không vẽ bàn tay — túi `hand1_L3`,
  `bag_R`), `hang_plan` (spec `hang` > param `hang` auto|none|list), key rotate = −tổng rotate tổ tiên, trễ 0.05s,
  đầu/cuối 0; luôn sinh. Rig thật: túi xoay world 96° → ~20°. Review render thêm lượt đeo túi ở khung túi cao nhất,
  rubric túi rủ xuống; review.json `hang`, `held`, `held_view`; modal hiện ảnh.
- [x] Op version spine_rig_summary/spine_add_anim/spine_weld_joints → "2" (tránh cache kết quả thuật toán cũ).
- [x] Test 25 trong file; pytest 461 pass / 4 skip; typecheck sạch.
- [x] Nghiệm thu cuối (wrap): plain c4678fe3 6.5 → 7.5 → 8; coat 029a4d56 7 → 7 → 8. Khuỷu 38–40°, cổ tay ≤40°,
  seam_visible false mọi vòng, hạ hông 2 lần, gối 53%/56%, túi treo; spine-core load OK, đầu/cuối = setup.
- Chưa commit, chưa build Portable.

## R10 + R11 — graph mượt/offset, ngón tay, váy mesh theo chuỗi xương váy + lò xo
- [x] R10: `_slopes` Fritsch–Butland cho mọi timeline rotate/translatex/y (mặc định `graph: smooth`, `ease` giữ kiểu
  cũ); `offset.arms`/`offset.hip_tilt`; `_drift_holds` (2 key bằng nhau → key sau trôi 35% về key kế, ≤3°); bù túi
  R9 lấy mẫu bezier thật. Prompt: lag thân 0.1, tóc ×1.35, không key bone được sinh.
- [x] R10 bổ sung: `_hands` (đầu cẳng tay mà bắp tay treo dưới bone không chiều dài gắn thân; bỏ chuỗi ≥3),
  `follow_plan`/`_follow_keys` (ngón trễ theo chuỗi tay + sóng co duỗi, tanh ±10/±8°, vào/ra 0.3/0.4s, 2 tay lệch
  pha) — tự dò ra đúng tham số h6; weld ép ngón (`name!`, `finger_joints`) → cùng 230 mảnh/hình học như JS.
- [x] R11: `ops/spine_skirt.py` + op `op.spine_skirt_mesh` (recipe chạy sau weld): chuỗi váy auto (≥4 bone, hướng
  xuống, gốc gần hông ≤10% chân), mảnh cứng trên hông/bone cha gốc chuỗi phủ ≥2 đoạn và xuống quá dải 70px → lưới
  ~28px, weight 4 đoạn gần nhất 1/(d²+40²), dải 70px trộn bone gốc. Rig này 9 mảnh = JS, lệch ≤0.004px. Anim
  `skirt_keys` (lò xo theo gia tốc ngang hông, counter = tổ tiên gốc chuỗi dưới hông, lệch pha chuỗi, fade) = H6
  ≤0.002°; bỏ chuỗi váy khỏi secondary và khỏi "Chains (secondary candidates)"; rig summary dòng "Generated, do not
  key".
- [x] Review: zoom bàn tay lúc tay vung nhanh nhất, 2 lượt mặc váy dài/ngắn (auto theo độ dài), rubric ngón/váy;
  review.json `hand`, `hand_zoom`, `skirt`, `skirt_views`; modal hiện ảnh. Op spine_skirt_mesh version 1.
- [x] Test +3 (28 trong file). pytest 464 pass / 4 skip; typecheck sạch.
- [x] Nghiệm thu (scratchpad `608ff0e6…/r10`): skirt_2 a94ac0be 7.5 → 8.0; dress_23 e17788f2 7.5 → 8.2; seam false;
  2 lần hạ hông; spine-core load OK, đầu/cuối = setup.
- idle/walk so gốc: lệch 0.03–0.07% pixel (dress_23, walk); skirt_2 idle 0.53% vì idle gốc có key xoay chuỗi váy ≤1.7°
  mà váy cứng gốc không theo, váy mesh giờ theo → không đạt ≤0.5px ở gấu váy (đúng ý đồ, đã báo).

## G1 — spine-anim-ref tổng quát cho rig khác (không chỉ spine-test)
- [x] Thân: `torso_bones` dò theo cấu trúc (hông = cha chung 2 chân IK → tên hip/hips/pelvis → cha của trục; trục =
  bone có chiều dài hướng lên ±45°, dừng ở bone tên `head` hoặc khi có ≥2 nhánh hướng lên = tóc); vai trò theo tên
  đúng rồi theo vị trí; mắt = bone có key scale blink sẵn, else tên chứa eye (không brow) dưới đầu. Rig summary dòng
  "Torso the sway drives"; schema spec thêm `bones` để Claude ghi đè; role thiếu thì bỏ qua (bounce không có hông →
  lỗi rõ). Chuỗi secondary bỏ bone thân và bone do constraint điều khiển (`driven_bones`: path, transform mix≠0,
  physics); rig summary dòng "Constraint-driven, do not key" + "Physics".
- [x] `skin_parts` mặc định `auto`: skin đầu mỗi nhóm, bỏ nhóm trùng slot với nhóm đã mặc (đầm vs quần), nhóm
  skin/body trước.
- [x] Runtime preview: `ops/spine_pose.py` port spine-core 4.2.119 (update cache theo `order`, inherit mode, IK đủ
  softness/stretch/compress/scale không đều, transform 4 kiểu, path constraint, timeline ik/transform/path, bezier
  9 điểm như runtime game; `sample(..., game=False)` giữ bezier chính xác cho phân tích chuyển động). Physics giữ
  thứ tự, không mô phỏng. Renderer thêm tint slot/attachment + blend additive.
- [x] Đo với spine-core (rig mẫu chính thức spineboy-pro, mix-and-match-pro, tach; mọi anim, bước 0.05s): lệch
  ≤1e-4px (chỉ khác đúng tại thời điểm key do float32 của spine-core).
- [x] "Biên độ đã chứng minh" `proven_bends`: khớp hard seam được nới giới hạn tới biên độ anim sẵn có của rig đã gập;
  weld `auto` bỏ cả rig nếu có khớp đã gập >45° (art mảnh cứng chồng nhau, vd spineboy: hàn làm méo giáp gối).
  Rig tach không đổi (khớp ≤34°): weld 12 khớp như cũ, anim H6 lệch ≤0.002°, idle/walk pixel như cũ.
- [x] Op version: rig_summary 3, add_anim 3, render_strip 2, weld 3, review loop 2. Test +4 (32 trong file); pytest
  468 pass / 4 skip.
- Còn lại: motion spec vẫn là kiểu "sway" (hông dẫn, thân theo, tay key) → plan `PLAN-spine-motion-types.md`.
- Chưa commit, chưa build.

## G2 — motion types (PLAN-spine-motion-types.md)
- [x] Spec `loop` / `keys` / `cycle`; `ops/spine_cycle.py` (walk/run tại chỗ, chân bám IK, hông tự hạ theo tầm với,
  tay ngược pha); `foot_contact` + loop check; review ghi chú chân, cycle không tính chồng gối; prompt chọn loại.
- [x] Thử spineboy-pro + mix-and-match-pro (scratchpad `37b6596d…/cmp_*.png`): trượt 0, lún 0, loop khớp.
- [x] Op version: rig_summary 4, add_anim 4, review loop 3. Test +3 (35 trong file); pytest 471 pass / 4 skip.
- [x] Preset `jump` (one-shot, về setup): ngồi gom lực chân bám đất → hông bật tăng tốc tới tầm với tối đa của chân
  (≤0.1L) → quỹ đạo parabol, gối co (chân lên nhiều hơn hông `tuck`) → tiếp đất giảm tốc, nhún 0.8 crouch → về setup;
  tay vung sau khi ngồi, vung trước-lên khi bật, cẳng tay trễ 0.06s; thân cúi khi ngồi/tiếp đất. `takeoff`/`land`
  (s) lấy từ video, mặc định 0.35/0.65 duration. Rig summary "Cycle (walk/run) and jump drive"; review không coi
  jump là loop. Thử spineboy-pro + mix-and-match-pro: setup đầu/cuối khớp, trượt 0, lún 0, gối ~65° ở đỉnh.
- [x] Op version: rig_summary 5, add_anim 5, review loop 4. Test +1 (36 trong file); pytest 472 pass / 4 skip.
- Chưa: preset attack (Claude key bằng `keys`), nghiệm thu live với video walk/run/jump, UI hiện foot check.
  Chưa commit.

## F1 — 3D game-ready (PLAN-feature-3d-gameready.md; brainstorm PLAN-2026-10-28-feature-ideas.md, chủ nhân duyệt 6 mục)
- [x] UI hộp kết quả chấm Spine: mục Kiểu chuyển động, Chân chạm đất (trượt/lún từng chân, đỏ khi lỗi), Key bị bỏ.
- [x] Worker `game_ready` + executor `blender.game_ready` (Lane.BLENDER, cache theo worker_hash): `remesh` (mặc định) =
  QuadriFlow, mesh TRELLIS bị QuadriFlow từ chối kể cả sau voxel → voxel remesh (diag/200) + decimate theo số tam
  giác, Smart UV, bake màu từ mesh gốc; `decimate` = giữ UV/texture gốc; `off` = cho qua. Bake normal + AO (Cycles
  GPU, selected-to-active), AO vào glTF qua node group "glTF Material Output". LOD1.. decimate từ LOD0.
- [x] Thử Blender 5.2 thật trên GLB TRELLIS 28.6k mặt: ~3s, LOD 3988/1987/987 tam giác, GLB có base/normal/occlusion;
  remesh 1k tam giác sạch, decimate 1k bị gai (ảnh so sánh scratchpad gr/compare.png).
- [x] Recipe prop-3d: bước `gameready` (input game_mode remesh|decimate|off, game_faces 5000, texture 2048), approve
  nhận thêm `game`/`maps`. Catalog kinds. Test +2; pytest 474 pass / 4 skip.
- [x] Thêm `<tên>_lods.glb`: mọi LOD trong 1 file chung 1 bộ texture, node `<tên>_LOD0..n` (quy ước LODGroup Unity).
- [x] Live E2E `scripts/e2e_r5.py prop-3d` (rương gỗ): succeeded 268s, gameready 7.5s, LOD 5000/2500/1250 tam giác,
  render LOD0/LOD2 gần như bản 29k (scratchpad chest/compare.png).
- Chưa: exporter Unity/Godot dùng `_lods.glb`; file LOD lẻ vẫn nhúng texture riêng (~8.7MB/LOD ở 2048). Chưa commit.

## F4 — anim Spine sẵn: attack / hit / death
- [x] `ops/spine_action.py`: spec `action = {type: attack|hit|death, at, facing, direction, ...}`, chân giữ IK.
  attack: lấy đà (tay cầm vũ khí = tay có nhiều bone con nhất vung sau, thân ngả, hông lùi) → chém nhanh (`strike`
  0.12s, vượt đà) → hồi; tay kia ngược 0.3. hit: giật lùi thân/hông, tay văng, nảy lại 1 lần. death: giật, khuỵu
  gối kiểu rơi (gia tốc), gập người, nảy nhẹ khi chạm, giữ tư thế cuối.
- [x] `hold` (spec + death mặc định): clip one-shot kết thúc ở tư thế cuối; `check_animation(hold=)` chỉ kiểm frame 0;
  keys tường minh không bị kéo về setup. Prompt recipe + review note + rig summary "Cycle (walk/run), jump and action".
- [x] Thử spineboy-pro + mix-and-match-pro (scratchpad rigs/act_*.png): chân không trượt; hit mặc định recoil 25°.
  add_anim v6. pytest 475 pass / 4 skip. Hạn chế: facing front không nghiêng thân (chỉ hông/tay).

## F6 — biến thể skin Spine (recipe spine-skin-variant)
- [x] `ops/spine_variant.py`: `op.spine_setup_render` (tư thế setup nền trắng) → `comfy.edit_flux2_klein4b` (mỗi
  dòng 1 biến thể, prompt "recolor only, giữ flat cel-shaded") → `gate.pick` (k=0, chọn nhiều) →
  `op.spine_skin_variant`: căn bbox ảnh sửa vào render, bản đồ "pixel thuộc region nào" từ `rt.layers` (renderer
  tách generator), mỗi region khớp thống kê màu YCbCr (mean/std, gain luma 0.7–1.4, chroma 0.4–3) trên pixel nội
  thất của nó; region vẽ nhưng bị che → map cả hình; region skin khác → giữ nguyên. Ra atlas `<tên>_vN.atlas` +
  page `_vN.png` (thay thẳng atlas gốc) + ảnh so sánh gốc|Klein|kết quả.
- [x] Lần 1 dùng affine least-squares → màu xỉn (hồi quy về trung bình) và prompt cũ để Klein vẽ lại kim loại thật →
  đổi sang khớp thống kê + prompt chỉ đổi màu. Live E2E mix-and-match 2 biến thể: 116s (Klein ~50s/ảnh), kết quả
  bám ảnh Klein, giữ nét (scratchpad e2e-skin2/both.png). Lỗi còn: mảng nhỏ lệch màu (balo, giày).
- [x] Test +1; pytest 476 pass / 4 skip. `scripts/e2e_skin_variant.py`. Chưa: skin mới trong cùng JSON (hiện là
  atlas thay thế), chọn vùng giữ nguyên (da, mặt).

## F2 — âm thanh (recipe audio-sfx, audio-music; gói model "audio")
- [x] Comfy adapter thu output audio (`AUDIO_EXT`, history kind "audio", outputs["audio"]). Workflow API
  `sfx_stable_audio` (Stable Audio Open 1.0 + t5-base, Comfy-Org repack không gate) và `music_ace_step15`
  (ACE-Step 1.5 turbo + qwen 0.6b/1.7b + vae, KSampler 8 bước). Manifest: pack `audio` + 6 model có sha256.
- [x] `ops/audio.py` `op.audio_post`: decode ffmpeg → cắt lặng (ngưỡng -40dB so đỉnh clip), fade, chuẩn hoá đỉnh,
  loop = bỏ đoạn đầu và crossfade equal-power vào đuôi (mẫu cuối nối mẫu đầu), xuất OGG (libvorbis) + WAV, ảnh
  dạng sóng; tên clip theo dòng mô tả (`labels` + `per_label`).
- [x] Live SFX: 3 loại × 2 bản trong 12s (Stable Audio ~2s/clip), dạng sóng/phổ hợp lý (whoosh dài, cót két nhiều
  transient, coin cao tần tắt dần). Music: chờ tải xong model ACE để chạy thật.
- [x] Test `tests/test_audio.py` (cắt/fade/chuẩn hoá, loop liền, ffmpeg ghi ogg/wav, đặt tên, recipe load).

## F5 — VFX flipbook (recipe vfx-flipbook; gói model "vfx")
- [x] Workflow `vfx_wan_alpha`: Wan 2.1 T2V 14B fp8 + LoRA lightx2v (6 bước, cfg 1) + LoRA Wan Alpha + 2 VAE
  (RGB, alpha) → JoinImageWithAlpha → PNG RGBA. Chạy được 12GB VRAM nhờ ComfyUI đẩy trọng số sang RAM (máy 64GB).
- [x] `ops/vfx.py` `op.vfx_frames`: oneshot = bỏ frame rỗng đầu/cuối + mờ dần 3 frame cuối; loop = đoạn có frame
  cuối gần frame đầu nhất (≥8 frame); crop vuông chung theo alpha → ô vuông → `op.sheet`. Test tổng hợp.
- [x] Live E2E (lửa phép, oneshot 33 frame 512²): 74.5s cả recipe, Wan 14B fp8 + lightx2v 6 bước chỉ 51s trên 12GB.
  Alpha thật, nền trong suốt sạch, quầng sáng mềm (scratchpad e2e-vfx/check.png). Model tự thêm khúc củi → prompt
  thêm "floating in empty space, no ground/objects". Quầng sáng mờ chạm mép nên crop gần cả khung.
- [x] App: viewer `audio` (AudioViewer: player + dạng sóng bấm để tua + nút Lặp), detect `isAudio`/`wave`.
- [x] #1 xuất Unity: có `_lods.glb` → kèm `Editor/AssetStudioLodGroup.cs` (menu Asset Studio > Make LOD Group).
  #6 thêm `keep` (mặc định eye,mouth) giữ màu region. Script e2e chung `scripts/e2e_text.py`.

## F3 — train LoRA style (spike)
- [x] Dataset 14 ảnh render Spine (spineboy + mix-and-match) + caption trigger `zsvstyle`, ComfyUI TrainLoraNode
  (mã hoá dataset → SaveTrainingDataset → /free → train chỉ UNET).
- [x] Z-Image Turbo 6B: bf16 768/512 và fp8+bypass đều OOM (PyTorch cấp 22.6GB, tràn RAM chia sẻ) → không khả thi
  trên 12GB. Klein 4B fp8: crash native trong backward (autograd abort, cả quantized_backward bật/tắt).
- [x] Klein 4B bf16 (tải thêm 7.75GB): train chạy được nhưng 142 s/bước (VRAM đầy, trọng số đẩy qua lại RAM) →
  300 bước ≈ 11 giờ. Dừng spike, xoá dataset thử (`input/studio_lora_spike`, `datasets/studio_lora_*`).
- Kết luận: train LoRA tại máy 12GB không thực dụng với ComfyUI TrainLoraNode. Hướng thay thế đề xuất: style theo
  ảnh tham chiếu (Klein nhận reference latent) cho Dự án, hoặc train trên cloud GPU ≥24GB. Chờ chủ nhân chọn.
- Ghi chú: `flux-2-klein-base-4b.safetensors` (bf16, 7.75GB) đã nằm ở F:/…/diffusion_models, không còn dùng — xoá được.
- [x] 2026-10-28 22:35 Handoff mới: `.planning/HANDOFF-2026-10-28-2235.md`

## Phiên 2026-10-28 22:4x (sau HANDOFF-2235) — chủ nhân: "tiếp tục làm các phần tiếp theo"
- [x] VFX chạy lại prompt mới (lửa phép oneshot, 56.5s, gen 47s): hết khúc củi/vật thể lạ. Phát hiện VAE alpha để
  vệt alpha rác (≤142) ở 2 hàng/cột mép mọi frame → vệt sọc đầu mỗi ô + bbox phình cả khung. `ops/vfx.py`
  `clear_border` (2px) trước khi xử lý, op v2; test thêm. Crop vẫn gần cả khung do tia lửa thật tới mép. pytest 483/4.
- [x] Action mặt trước (`facing: front`): thân nghiêng ngang 35% biên độ theo `direction` (xoay theo màn hình,
  không dùng sign hướng tới); hip vẫn không trượt ngang. `spine_action.FRONT_TILT`; test so front < side. pytest 483/4.
- [ ] Còn: skin variant balo/giày lệch màu (cần e2e + soi preview); Godot LOD bỏ qua (Godot tự sinh LOD).
- [x] #3 chốt (chủ nhân: chỉ có GPU 12GB) → bỏ train LoRA/cloud; làm style theo ảnh tham chiếu, không train.
  Recipe `concept-2d-style`: ảnh tham chiếu (Dự án tự điền `reference_image` qua `apply_style`, prompt được bọc
  prefix/suffix style) → `op.trim_pad_resize` nền trắng vuông 1024 (tham số mới `background`) → Klein
  ReferenceLatent, mỗi dòng 1 vật thể → pick → rmbg → fit → sheet. Live: 3 vật thể 141s (~43s/ảnh), ra 1024²,
  không chép nhân vật/mũ của ảnh mẫu; khớp viền nâu, cel shading, bảng màu; chất nét tay riêng khớp vừa phải.
  Ảnh: scratchpad style_check2.png. Script `e2e_text.py` nhận input "@file" (upload) + gate pick chọn hết. pytest 483/4.
- [x] Commit (chủ nhân duyệt): 82cabb2 spine, 03359f9 3D game-ready, d0ddfea audio/VFX/style, 71608c2 planning.
  Gộp 4 nhóm vì test/kinds/ops __init__ dùng chung. Chưa push.
- [x] Xoá Klein bf16 F:/work/AI/comfyUI_models/diffusion_models/flux-2-klein-base-4b.safetensors: auto mode chặn
  (xoá không hoàn tác) → chủ nhân tự chạy, đã xoá.
- [x] Skin variant lệch màu balo/giày: tâm thống kê mean → median (viền/nền lọt vào vùng do ảnh sửa căn không khớp).
  Đo trên edit Klein thật (scratchpad var_eval.py): sai số màu trung vị theo region 28.1 → 9.0; IQR thử bị hỏng ở
  vùng phẳng (hair-patch ra xanh ngọc) nên giữ std. op v3. pytest 483/4. Chưa commit.
- [x] Commit 728354a (skin variant median).
- [x] Nhịp nhạc: cách đo cũ sai. Đo comb autocorrelation (scratchpad bpm2.py): yêu cầu 80→80, 140→140 (70 nửa nhịp),
  clip 96 cũ→95. ACE-Step theo đúng bpm.
- [x] Loop nhạc tròn ô nhịp: `make_loop(a, xfade, bar_s)` cắt đuôi để (độ dài − crossfade) = số nguyên ô 4/4 → phách
  đầu/đuôi cùng pha khi trộn. `op.audio_post` param `bpm` (v2), recipe audio-music truyền bpm. Live 140 BPM 30s →
  27.429s = 16 ô, seam 0.0042. pytest 483/4. Chưa commit.
- [x] VFX loop live (vòng năng lượng xanh): đoạn 7..28 (21 frame), seam 0.045, crop gọn [20,22,495,497] — đẹp.
  Prompt "small, middle half, margins" làm model vẽ thêm khối vật cháy → bỏ, giữ prompt cũ. `square_crop` theo 99.5%
  khối alpha (tia lửa lạc không kéo crop ra hết khung), op v3; lửa nổ vẫn gần cả khung vì quầng sáng thật trải rộng.
- [x] Docs (subagent): README bảng "Làm được gì" + đoạn game-ready/âm thanh/VFX + dung lượng pack audio ~15.8GB,
  vfx ~22.5GB; COMFYUI-WORKFLOWS mục 8–10. Chưa commit.
- [~] Subagent review code đợt này (chỉ báo lỗi).
- [x] Review subagent: 12 phát hiện, sửa hết (pytest 485/4, typecheck sạch):
  1 `foot_drivers` hai chân chung driver khi target cùng nhóm → dừng leo ở bone tổ tiên của chân kia (test fail trước sửa);
  2 skin variant tint slot bị áp 2 lần → `rt.layers` yield thêm màu tint, `colour_maps` chia tint cả src/dst (op v4, test);
  3 retopo hỏng làm hỏng mesh cho fallback → retopo trên bản sao; 4 slot vật liệu rỗng xen kẽ → gán blank; bỏ
  `principled` trùng (bản sau đã tự tạo BSDF); 10 tên LOD cắt stem 50 byte. Blender live: remesh 3988 / decimate 3995 mặt OK;
  5 text default stride 1.15/1.5 → 0.8/1.2 theo CYCLE_DEFAULTS; 6 loop keys sau end → SpecError;
  7 audio rỗng → StepError (test); 8 VFX loop ≤8 frame → StepError; 9 atlas region index trùng tên → `Atlas.every`
  (test); 11 audio loop thêm hậu tố `_loop` (op v3) để player bật lặp; 12 nhãn attack/hit/death trong ReviewDialog.
  Chưa commit.
- [x] Commit dbbb9d8 (nhạc/VFX/docs) + 5d2621a (12 lỗi review).
- [x] Skin variant e2e lần 3 (kiểm lâm, 62s): mũ/áo/quần khớp; balo bị xanh lá, giày tím. Nguyên nhân: (a) 49% pixel
  balo là dải lộ giữa tay và thân, Klein vẽ áo lấp chỗ đó → `_main_part` chỉ học từ mảng liền lớn nhất; (b) region
  balo chứa cả túi ngủ nâu + thân navy → cụm màu k-means (≤4, ≥3%, gộp <0.06) mỗi cụm 1 map, texel pha mềm theo
  khoảng cách màu; (c) gain chroma trần 3 làm cụm navy phẳng thành xanh bão hoà → trần 1.5. Soát mắt 2 edit thật:
  balo/túi ngủ/giày đúng cả bản đỏ lẫn kiểm lâm. Op v6, test 2 màu. (Số đo median cả region không còn hợp lệ vì
  tính cả vùng bị che.)
- [x] Subagent khảo sát chỗ liệt kê cứng + subagent sửa: pitch/art/nhóm "Âm thanh" cho recipe mới, gallery lọc audio,
  badge+glyph audio, nhãn kind/bước (Sinh âm thanh, Sinh video VFX, Render tư thế setup), tên port; kinds.py: output
  SaveAudio = audio file[] (trước bị coi là mesh → lint cảnh báo), nhãn 3 workflow, param bpm. pytest 486/4, typecheck sạch.
- [ ] Còn (khảo sát): xuất Unity/Godot chưa nhận audio, flipbook (final thiếu meta), skin variant (thiếu skeleton) —
  chỉ xuất thô được. Chưa commit đợt này.
- [x] Xuất engine cho đồ mới:
  audio: `Source.audio` (1 file/clip, ưu tiên OGG), Unity/Godot chép `Audio/` + README cách bật Loop cho *_loop;
  `engine_ready` tính cả audio. Live: audio-music 20s → xuất Godot OK (Audio/…_loop.ogg + README).
  flipbook: `op.sheet` param `flipbook` (oneshot|loop) + `fps` → meta.flipbook, pivot giữa ô; `layout` thành 1 track
  đọc theo hàng; vfx-flipbook outputs final/meta = sheet. Live: VFX loop → xuất Unity: clip 16 frame 24fps LoopTime 1.
  skin variant: op trả thêm `skeleton` + `texture` (trang v1), approve chuyển tiếp → engine thấy Spine (unit test).
  `e2e_text.py` tham số thứ 5 = engine để xuất luôn. pytest 488/4, typecheck sạch. Chưa commit.
- [x] 2026-10-29 00:07 Handoff mới: `.planning/HANDOFF-2026-10-29-0007.md`

## Phiên 2026-10-29 (sau HANDOFF-0007)
- [x] Kiểm thật xuất biến thể skin: `e2e_skin_variant.py` thêm tham số 5 = engine (xuất bước approve). mix-and-match-pro
  65s/run; Unity → `Spine/` json + atlas.txt (trỏ `mix-and-match_v1.png`) + trang `_v1` + README; Godot → `.spine-json`
  + `.atlas` + trang `_v1`. Đạt.
- [x] VFX xuất engine: track flipbook không có hướng (`Track.dir=""`, `Layout.facing`) → clip tên `<clip>` (không còn
  `sheet_S.anim`); README Unity/Godot riêng cho flipbook (pivot giữa ô); Godot bỏ `asset_studio_sprite.gd` + script
  trong .tscn khi không có hướng. Test `test_flipbook_export_has_no_facing`. pytest 489 pass / 4 skip.
- Bỏ qua: LOD riêng cho Godot (Godot tự sinh), ảnh art recipe audio/VFX (icon fallback, chỉ thẩm mỹ).
- [x] Godot LOD: `*_lods.glb` → `<stem>.tscn` (Node3D + instance GLB) + `asset_studio_lods.gd` (@tool, visibility range
  cho node `*_LODn`, LOD n bắt đầu ở `lod_distance`×2^(n-1), mặc định 10m) + README nhắc tắt Generate LODs. Kiểm Godot
  4.7.2 headless (`L:\AI\godot-test\LodLive`, `lod_check.gd`): 3 LOD 0–10/10–20/20–∞, fails=0. pytest 490 pass / 4 skip.
- [x] Art recipe: 9 webp mới (audio-sfx/music, vfx-flipbook, spine-anim-ref, concept-2d-style, spine-skin-variant,
  sprite-8dir-anim/pixel/3d-pixel) qua `app/scripts/gen-art.py` (Codex), `art/index.ts` + pitch spine-anim-ref; typecheck sạch.
- [x] Nghiên cứu: `.planning/PLAN-2026-10-29-feature-ideas.md` (12 đề xuất, chờ chủ nhân duyệt).
- [~] 2026-10-29 00:3x Đợt 1 giao 4 subagent: #1 dùng tiếp, #7 upscale ESRGAN, #2 manifest GDD, #3 UI kit. Push xong (1046c2b).
- [x] 2026-10-29 00:39 Handoff mới: `.planning/HANDOFF-2026-10-29-0039.md`
- [x] 2026-10-29 #1 "Dùng tiếp cho…" xong (agent): `engine/chain.py` (`$meta.then`), runner `_start_chain`, recipe
  `spine-character-image`, `GET /api/blobs/{hash}/info`, app `followUp.ts` + `FollowUpMenu.vue` + NewRunView `input.*`.
  `tests/test_chain.py` 12 pass; typecheck còn 1 lỗi `mock/server.ts:1506` thuộc #2 (chờ agent #2).
- [x] 2026-10-29 #7 Upscale ESRGAN xong (agent): `RealESRGAN_x4plus` (BSD-3, có sẵn F:), workflow `upscale_esrgan`,
  `ops/upscale.py` (prep/post, alpha Lanczos riêng, pixel art nearest, atlas scale), recipe `upscale`, pack `upscale`
  manifest. `test_upscale.py` 11 pass; live 3 run OK (rương x4 17.6s, VFX x2, coin nearest), VRAM ~0.6GB.
  Chưa live atlas (e2e_text 1 file); toàn pixel art vẫn khởi động comfy ~12s.
- [x] 2026-10-29 #2 Danh sách asset từ GDD xong (agent): bảng `manifests` (db/repo), `engine/manifest.py` (row,
  status, CSV/YAML), `adapters/gdd.py` (Claude ≤30 dòng), `api/manifest.py` (`/api/projects/{id}/manifest` GET/PUT,
  from-gdd, import, export, run), batch per-item `recipe_id`/`meta` (`routes.start_batch`, áp style trước khi check),
  MCP `get_asset_list`/`asset_list_from_gdd`/`run_asset_list`, app tab "Danh sách asset" (`ManifestPanel.vue`).
  `test_manifest.py` 8 pass; live GDD mẫu → 18 dòng/38s/$0.085 Opus. Typecheck sạch (đã sửa mock:1506).
- [~] 2026-10-29 Đợt 2 giao song song (không chờ #3): #5 QA dự án, #10 rig 3D, #9 PBR, #4 engine preview (Godot+Unity batchmode).
  Full pytest + typecheck chạy nền để kiểm đợt 1.
- [x] 2026-10-29 Kiểm đợt 1 (#1/#7/#2, #3 dở): pytest 528 pass / 4 skip; typecheck sạch.
- [x] 2026-10-29 #3 UI kit xong (agent): `ops/ui.py` (`op.ui_split`, `op.nine_slice` + states hover/pressed/disabled),
  recipe `ui-kit`, `ui-kit-style`, `icon-pack`; Unity spriteBorder + `UI/` README, Godot StyleBoxTexture + `<asset>_ui.tscn`.
  `test_ui_kit.py` 7 pass, full 528 pass. Live: ui-kit 36s, ui-kit-style 53s, icon-pack 6 icon ~350s.
  Sửa `scripts/e2e_text.py:49-51` (gate k>0 chọn k đầu). Còn: ngọc giữa cạnh → off_center, chưa mở engine thật, chưa art.
- [x] 2026-10-29 #9 PBR xong (agent): `ops/pbr.py` (`op.pbr`), `export/pbr.py` (Unity URP Lit .mat + PBR/, Godot StandardMaterial3D),
  recipe `pbr-material`, texture-seamless input `pbr`. `test_pbr.py` 8 pass, full 536 pass. Chưa mở engine thật.
- [x] 2026-10-29 Nghiên cứu lượt 2 → #13–#22 (plan file). **Chủ nhân chốt làm cả 10** + cho tự chạy đêm, **được commit**
  từng phần khi test xanh (KHÔNG push). Hàng đợi: 13,14,15,16,17,18,19,20,21,22 rồi đợt 3 (#8+12, #6 local, #11 spike).
  Tối đa ~4 agent, GPU khoá bằng `mkdir` thư mục `L:/AI/AI-Asset-Studio/.gpu-lock` (agent tạo trước live run, xoá sau).
- [x] 2026-10-29 #5 QA đồng bộ dự án xong (agent): `engine/project_qa.py` (palette OKLab/tỉ lệ/viền/hướng sáng),
  `adapters/project_qa.py` (Claude vision), router `/api/projects/{id}/qa` (+`/fix` → variants-2d), MCP `project_qa*`,
  tab "Kiểm tra đồng bộ" `QaPanel.vue`. 6 test pass. Heuristic chưa thử asset thật.
- [~] 2026-10-29 Đang chạy: #10 rig 3D, #4 engine preview, art 5 recipe, #13 collider, #14 rarity. Chờ: #15–#22, đợt 3.
- [x] 2026-10-29 Art 5 recipe mới xong (upscale, ui-kit, ui-kit-style, icon-pack, spine-character-image).
- [x] 2026-10-29 02:30 Kiểm: pytest 560 pass / 4 skip, typecheck sạch → commit mốc. Handoff mới: `.planning/HANDOFF-2026-10-29-0230.md`
