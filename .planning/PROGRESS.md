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
