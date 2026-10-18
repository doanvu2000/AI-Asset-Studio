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
