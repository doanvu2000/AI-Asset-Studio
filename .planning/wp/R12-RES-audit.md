# R12-RES audit: hiệu năng, UI/UX, quy trình (2026-11-03, chỉ đọc/đo, không sửa code)

Ghi chú đo: máy đang chạy nhiều pytest song song nên số tuyệt đối của test bị phóng đại; số import/bundle là thật.

## 1. Hiệu năng

| # | Phát hiện | Đo được | Đề xuất | Công | Ưu tiên |
|---|-----------|---------|---------|------|---------|
| H1 | Bundle chính `app/out/renderer/assets/index-*.js` 679 KB (nặng nhất sau three 1.3 MB lazy). Chứa runtime `motion-v` (framer layout-projection) vì `App.vue:4` (MotionConfig), `AppSidebar.vue:5`, `RunDetailView.vue:4` import tĩnh | 234 file, 7.2 MB renderer; index = ~10% tổng | Thay MotionConfig/motion.* ở sidebar bằng CSS transition + `useReducedMotion` có sẵn trong `composables/motion.ts`; RunDetail dùng `defineAsyncComponent` cho phần AnimatePresence. Kỳ vọng index giảm ~150-250 KB | M | P2 |
| H2 | `GET /api/versions` mặc định `limit=200` (`store/repo.py:240`, `api/routes.py:418`), store gọi `api().versions()` không phân trang (`stores/versions.ts:62`) | Dự án >200 version: Gallery/Gates/đếm thiếu âm thầm, không có dấu hiệu "còn nữa" | Thêm `before`/`offset` + `X-Total`, store tải trang kế khi cuộn gần cuối, hoặc gallery query theo `project_id` ở server | M | P1 |
| H3 | `routes.py:370-379` list_runs gọi thêm `run_preview_hashes` mỗi lần; `RunsView` virtualize tốt nhưng `limit` tới 2000 (`stores/runs.ts:177`, recompute QA `GalleryView.vue:212` limit 5000 một phát) | Chưa đo được (không có DB lớn) | Chunk recompute QA 500/lần có tiến độ; giữ PAGE_SIZE cho list | S | P3 |
| H4 | Import core: `python -c "import studio_core"` 0.17 s (tốt). `import studio_core.api.routes` kéo `engine.kinds` (catalog hơn 1400 dòng dict) ~130-160 ms + fastapi 181 ms (số lần chạy nóng). Lần đầu nguội 3-7 s do disk | Warm: cây import engine ~165 ms | Không cần sửa; chỉ cần splash che lần đầu. Đã có `SplashScreen` | - | P3 |
| H5 | Thumbnail: `store/blobs.py:151` cache file + lock + `to_thread` + Cache-Control immutable: đã tốt. Không phát hiện đọc ảnh lặp trong API | - | Không đổi | - | - |
| H6 | SQLite: WAL + synchronous NORMAL, index đủ cho runs/steps/versions. Thiếu index hợp `versions(project_id, status, created_at)` cho lọc Gallery theo status (hiện chỉ idx_versions_project + project_mime) và `steps(status)` đã có | Chưa đo (DB nhỏ) | Thêm composite `idx_versions_project_status(project_id,status,created_at DESC)` trong migration | S | P3 |
| H7 | Polling: chỉ `SetupPanel.vue:64` (1 s, chỉ khi cài), `RegionEditDialog.vue:175` poll getRun, `agents.ts:21` 30 s. Còn lại dùng WS có resume (`api/ws.ts`) | Không có polling thừa ngoài 2 chỗ trên | RegionEdit nên nghe WS `run.*` thay vì poll; SetupPanel giữ | S | P3 |
| H8 | List dài: Gallery và Runs dùng `@tanstack/vue-virtual` (tốt). `FlowBatchView`/`BatchDetailView` (matrix lượt x stage) và `ManifestPanel` render đủ, không virtualize | Batch vài trăm lượt x 6 stage = >1500 cell DOM | `content-visibility:auto` cho `.mrow` (một dòng CSS) hoặc vue-virtual khi >150 dòng | S | P2 |
| H9 | Test suite 1651 test (`core/tests`, asyncio auto). Không có `pytest-xdist`, không có marker slow. File nặng đo được: `test_r8_style.py` 15 test = 54 s (restyle 16 s, clip 9.6 s, finish 8.7 s, off_style 6 s); `test_p4b_mcp.py` spawn core 3 s/test. Full suite lúc đo >15 phút do tranh CPU | Phần còn lại phần lớn <0.5 s | (a) cài `pytest-xdist`, chạy `-n 4` (`--dist loadfile` vì fixture SQLite/tmp_path theo file); (b) đánh `@pytest.mark.slow` cho test >5 s + `addopts = -m "not slow"` cho vòng nhanh, CI/release chạy đủ; (c) test_r8_style dùng ảnh 64 px thay ảnh lớn | S | P1 |

## 2. UI/UX

Nền tốt: tokens.css + base.css có `focus-visible`, ~300 aria/role trong 21 view, EmptyState dùng ở Gates/Batch/Recipe, Gallery có virtualizer, Ctrl+S ở FlowEditor/RecipeEditor, `onBeforeRouteLeave` chặn rời khi chưa lưu. Khoảng trống:

| # | Phát hiện | Đề xuất | Công | Ưu tiên |
|---|-----------|---------|------|---------|
| U1 | `FlowCanvas.vue` + `FlowEditorView.vue`: không có phím Delete/Backspace xoá node, không có Ctrl+Z/Y (grep không thấy) | Thêm onKeyStroke Delete (có confirm nếu node có dây), undo stack đơn giản cho thao tác thêm/xoá/nối | M | P2 |
| U2 | `AuditView.vue` (22 KB) và `RestyleView.vue` (29 KB): chỉ có `scanning` ref, không có EmptyState khi quét xong 0 kết quả, không skeleton; lỗi hiện qua `.err.callout` thay vì EmptyState chuẩn | Dùng `EmptyState` cho: chưa chọn thư mục / không có vấn đề / lỗi quét; hiện số tệp đã quét khi `scanning` | S | P2 |
| U3 | `GalleryView.vue:440-500`: bộ lọc 3 hàng (loại, trạng thái, thấp điểm+sắp xếp+tìm) chiếm cao trước lưới ảnh | Gộp 1 hàng: tìm + chip "Lọc" mở popover chứa loại/trạng thái/sắp xếp, hiện số bộ lọc đang bật + nút xoá lọc. Giữ `role=radiogroup` | M | P2 |
| U4 | `PageHeader.vue`: nhãn nút tự rút gọn khi `.compact` (TITLE_MIN=200) nhưng `fit()` chỉ chạy qua ResizeObserver trên root/actions: nút thêm sau khi load (vd Gate count, dirty) có thể tràn một nhịp | Gọi `fit()` trên `nextTick` khi slot đổi (MutationObserver nhẹ) | S | P3 |
| U5 | Phím tắt: CommandPalette liệt kê Ctrl 1-8, Ctrl N, Ctrl K. `RunDetailView:180` ←/→ đổi bước; `GateApprove/GatePicker` có onKeyStroke. Thiếu: `?` mở bảng phím tắt toàn cục, `g` rồi `r` kiểu đi tới, Ctrl+Enter ở FlowStart (chỉ NewRun có `NewRunView.vue:480`) | Thêm Ctrl+Enter ở `FlowStartView` + AuditView/RestyleView (hiện chỉ Enter ô đường dẫn) | S | P2 |
| U6 | `RecipeEditorView`/`FlowEditorView`: cột inspector `aria-label="Thuộc tính"`, canvas không có alternative text bàn phím (không tab giữa node) | `tabindex=0` cho `StepNode`, Enter chọn, mũi tên chuyển node lân cận | M | P3 |
| U7 | Chuỗi cứng: app một ngôn ngữ (tiếng Việt), không có i18n; chỉ còn lẻ tẻ tiếng Anh ở nhãn kỹ thuật (Seed, LOD, VRAM). Không cần i18n; chỉ cần rà `Seed (-1 = ngẫu nhiên)` kiểu nhãn ở `engine/kinds.py:1185,1261` cho nhất quán | Chuẩn hoá nhãn seed: "Seed (-1 = ngẫu nhiên, 0 = theo bản đồ)" một kiểu | S | P3 |
| U8 | Loading: `NewRunView:559` chỉ có dòng chữ "Đang tải quy trình…", không skeleton | Skeleton card 6 ô như `GateTile` | S | P3 |

## 3. Quy trình (mở app đến asset trong project Flutter)

Đường hiện tại (đã có project, engine đích Flutter): Ctrl+N -> chọn quy trình -> điền prompt (project tự điền style `NewRunView:287 seedFromProject`) -> Ctrl+Enter -> RunDetail -> "Cần duyệt" (Gate pick, mục `/gates`) -> chọn ảnh -> chạy tiếp -> output -> "Xuất cho engine" -> chọn thư mục. Tối thiểu 8-9 thao tác, 3 màn khác nhau.

| # | Phát hiện | Đề xuất | Công | Ưu tiên |
|---|-----------|---------|------|---------|
| W1 | Không có "Chạy lại" với cùng input ở `RunDetailView`/`RunsView` (chỉ retry từng bước lỗi `RunDetailView:148`). Chạy lại lượt đã xong phải về Tạo mới, nhập lại prompt | Nút "Chạy lại" (cùng input + params) và "Chạy lại, seed mới" trên header RunDetail; bắn `POST /runs` với `inputs/params` của run cũ (đã có trong run row) | S | P1 |
| W2 | Seed: `engine/kinds.py` có `seed` rải rác ở ~15 step nhưng đều `advanced`, giá trị mặc định cố định (7 hoặc 0) hoặc -1 ngẫu nhiên không thống nhất. Không hiện seed đã dùng trong RunDetail, nên không "chạy lại đúng seed cũ" cũng không "giữ prompt, đổi seed" được | Ghi seed thực dùng vào `step.meta` + hiện trong StepInspector kèm nút sao chép; GatePicker "tạo lại" đã nói "seed mới" (`GatePicker.vue:82`) nên thêm tuỳ chọn "cùng seed" | M | P1 |
| W3 | Không có preset/"công thức mẫu" lưu theo người dùng: `PresetSelect` chỉ chọn preset kiểu (style). Form 30+ input phải điền lại mỗi lần cho cùng recipe | "Lưu thiết lập này" (tên) trong NewRun, danh sách preset theo recipe + theo project (SQLite bảng `run_presets`), chọn xong điền form | M | P2 |
| W4 | Batch: có `mode=batch` (Nhiều prompt) và FlowStart batch; thiếu batch "1 prompt x N seed" (biến thể) — chỉ vary theo prompts/files (`NewRunView:642-646`) | Thêm `varyBy = 'seeds'` (N lượt, seed tăng dần) | M | P2 |
| W5 | Gate: GateApprove/GatePicker có chế độ one/all (`GatesView:98`) nhưng mỗi lượt vẫn dừng chờ người. Recipe có `gate_policy` cho agent nhưng UI NewRun không có công tắc "Tự duyệt ứng viên tốt nhất (QA cao nhất)" | Công tắc "Tự chọn ảnh điểm QA cao nhất" khi chạy batch (gate auto_first đã có ở MCP: `mcp/server.py`), kèm cảnh báo | M | P1 |
| W6 | Xuất: sau khi chạy xong phải sang bước riêng "Đưa vào thư viện" + "Xuất cho engine"; không có "Tự xuất vào project Flutter khi duyệt xong" | Trên project: tuỳ chọn "Tự xuất khi duyệt" (engine đích + thư mục đã lưu trong project), RunDetail chỉ hiện banner "Đã xuất vào ..." | M | P2 |
| W7 | README "Dùng lần đầu" 4 dòng, không nêu đường project -> Flutter, không nêu Ctrl+Enter / batch / preset | Sau khi có W1-W6, cập nhật README + WhatsNew | S | P3 |

## Gói việc đề xuất (không chồng file)

| Gói | Nội dung | File chính | Công | Ưu tiên |
|-----|----------|------------|------|---------|
| A. Test-speed | H9: xdist, marker slow, thu nhỏ ảnh test_r8_style | `core/pyproject.toml`, `core/tests/test_r8_style.py`, `conftest.py` | S | P1 |
| B. Run again + seed | W1, W2: nút chạy lại, hiện/lưu seed | `views/RunDetailView.vue`, `components/flow/StepInspector.vue`, `engine/runner.py` (meta seed) | M | P1 |
| C. Versions phân trang | H2, H6, H3 | `store/repo.py`, `api/routes.py`, `stores/versions.ts`, `store/db.py` | M | P1 |
| D. Batch nhanh | W4, W5, W3: vary seeds, auto-pick, preset người dùng | `views/NewRunView.vue`, `stores/catalog.ts`, `store/repo.py` (+ bảng run_presets) | L | P1/P2 |
| E. Export tự động | W6 (+W7 README) | `views/ProjectDetailView.vue`, `components/ExportToEngine.vue`, `README.md` | M | P2 |
| F. Gallery và editor UX | U1, U3, U5, U6 | `views/GalleryView.vue`, `components/flow/FlowCanvas.vue`, `views/FlowEditorView.vue`, `views/FlowStartView.vue` | M | P2 |
| G. Audit/Restyle states + bundle | U2, U4, U8, H1, H8, H7 | `views/AuditView.vue`, `views/RestyleView.vue`, `components/PageHeader.vue`, `App.vue`, `components/AppSidebar.vue`, `views/BatchDetailView.vue`, `components/RegionEditDialog.vue` | M | P2 |

Lưu ý chồng file: D và B cùng đụng `engine/runner.py` / `repo.py` nếu làm cùng lúc; C và D cùng `repo.py` => làm C trước D, hoặc tách migration riêng.
