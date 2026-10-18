# UI-AUDIT — AI Asset Studio (2026-10-18)

Khảo sát read-only bản `npm run dev:mock` (Electron, CDP port 9351). Đã dùng skill `frontend-design` + `ui-ux-pro-max`
và đối chiếu Apple HIG + PLAN-architecture-v2 §6.
Ảnh chụp: `C:\Users\dauph\AppData\Local\Temp\claude\L--AI-AI-Asset-Studio\5172a7e0-42f0-4e58-9349-f4cca3791827\scratchpad\uiaudit\shots\`
(viết tắt `shots/` bên dưới; hậu tố `-L` = sáng, `-D` = tối, `-1600` = 1600×1000, còn lại là 1280×800).
Ảnh tổng hợp: `…\uiaudit\contact-sheet.png`.

> Lưu ý khi đọc ảnh: dữ liệu là mock. Sidebar ở ảnh tối trông xám vì lúc chụp bằng CDP, Mica không được vẽ
> (`--bg-sidebar` vốn trong suốt). Cần kiểm lại trên cửa sổ thật trước khi kết luận về màu sidebar ở theme tối.

---

## 1. Tóm tắt: 8 vấn đề lớn nhất

1. **Không có bước "lấy asset ra" (xuất / mở thư mục / kéo thả).** Flow tạo → duyệt → **xuất** bị đứt ở bước cuối:
   run xong, Thư viện, lightbox đều không có nút Xuất, Mở trong thư mục hay Copy đường dẫn (grep renderer không có
   `showItemInFolder`/save). Đây là lỗ hổng lớn nhất về khả năng dùng.
2. **Sơ đồ (graph) của Run detail và Recipe editor bị cắt, không fit.** Node thứ 3 trở đi nằm khuất dưới inspector,
   canvas trống rất rộng, minimap chỉ hiện vài vạch màu (log có lỗi SVG `NaN` từ vue-flow). Thế mà đây lại là
   view mặc định. (`run-ok-L`, `run-graph-1600`, `recipe-editor-L`)
3. **Kết quả không phải nhân vật chính.** Run hoàn tất không có khối "Kết quả" ở đầu trang: người dùng phải bấm
   từng step mới thấy output. Các dòng run ở Tổng quan, Lượt chạy và Cần duyệt chỉ có chữ, **không có thumbnail**,
   dù đây là app làm ảnh/asset.
4. **Lộ thuật ngữ kỹ thuật cho người dùng:** `gen`, `pick`, `rmbg`, `gate.pick`, `op.trim_pad_resize`,
   `comfy_node_errors`, `interrupted`, "Recipe", "Mã recipe", `${inputs.ten}`, "71 run", "Huỷ run", nhãn hướng
   `down_right`. Tiêu đề cột trong bảng hàng loạt cũng là id step (`gen/pick/rmbg/fit`).
5. **Phải đổi màn để duyệt.** Run đang dừng ở bước "Chọn ảnh" có hiện 4 ảnh nhưng không chọn được tại chỗ, phải
   bấm "Mở hộp duyệt", sang màn khác, rồi "Xem run" để quay lại. Bước chọn ảnh cũng thiếu "Không ảnh nào ổn → tạo lại".
6. **Badge và trạng thái mâu thuẫn:** sidebar "Lượt chạy 4" nhưng tab "Đang chạy 0"; "Thư viện 23" nhưng header ghi
   "47 phiên bản"; Tổng quan có mục "Đang chạy" mà cả 4 dòng đều là "Chờ duyệt", lặp lại banner "4 bước đang chờ" ngay
   phía trên. Có 4 badge với 3 màu khác nhau và ý nghĩa khác nhau, gây nhiễu.
7. **Thứ bậc chữ bị ngược và không thống nhất:** tiêu đề trang (toolbar) 17px nhưng tiêu đề nội dung 26px bold
   ("Concept 2D (Z-Image)", "Studio (mặc định)", "Đường dẫn công cụ"). Có chỗ 10–11px hard-code (StepNode,
   FlowCanvas, SpineViewer). Font mono bị dùng cho chữ thường (phụ đề menu Cài đặt).
8. **Lỗi và khôi phục còn yếu:** cùng một lỗi lặp 3 lần trên màn (banner + card step + inspector) với 2 nút "Thử lại",
   mà không gợi ý cách sửa (vd OOM thì "Tắt Blender / giải phóng VRAM rồi thử lại"). Màn Core offline chỉ có
   "Copy nhật ký", không có "Khởi động lại core" hay "Mở Cài đặt đường dẫn". CoreBanner đẩy toolbar xuống, làm lệch
   vùng kéo cửa sổ và nút caption.

---

## 2. Bảng phát hiện

| # | Màn | Vấn đề | Mức | Ảnh | Đề xuất cụ thể |
|---|-----|--------|-----|-----|----------------|
| 1 | Run detail / Thư viện / Lightbox | Không có cách xuất asset (Xuất…, Mở thư mục, Copy path, kéo ra Explorer) | **Cao** | `run-ok-L`, `lightbox-img-D`, `gallery-L` | Thêm thanh hành động: **Xuất…** (chọn preset Unity/Godot/Studio), **Hiện trong thư mục**, **Copy đường dẫn**, kéo thả file (`webContents.startDrag`). IPC mới ở main, có whitelist đường dẫn blob |
| 2 | Run detail (Sơ đồ), Recipe editor | Graph không fit, node bị inspector che, minimap hỏng (`NaN` trong `<pattern>`/`<circle>`) | **Cao** | `run-ok-L`, `run-graph-1600`, `recipe-editor-L` | Gọi `fitView({padding:.2})` sau khi layout và khi pane resize (ResizeObserver). Bỏ minimap khi ≤ 12 node. Ở chiều rộng < 1440 thì mặc định chế độ **Danh sách** |
| 3 | Run detail (hoàn tất) | Không có khối "Kết quả" ở đầu, output nằm trong step cuối | **Cao** | `run-list-1600`, `run-sprite-graph-1600` | Thêm "Result hero" ở đầu inspector khi run `succeeded`: viewer output `final` + nút Xuất + Duyệt vào thư viện. Step được chọn chuyển xuống dưới |
| 4 | Tổng quan, Lượt chạy, Cần duyệt (list trái) | Dòng không có thumbnail | **Cao** | `dashboard-L`, `runs-L`, `gates-L` | Thumbnail 40×40 (checker) ở đầu dòng: output cuối, hoặc ảnh live khi đang chạy. Có sẵn `thumbUrl()` |
| 5 | Run detail (chờ chọn ảnh) | Thấy 4 ảnh nhưng không chọn tại chỗ được, phải nhảy sang Cần duyệt rồi quay lại | **Cao** | `running-2`, `run-awaiting-L` | Nhúng component chọn/duyệt của GatesView vào inspector (dùng chung). "Mở hộp duyệt" chỉ là đường phụ |
| 6 | Cần duyệt (chọn ảnh) | Không có lựa chọn "Không ảnh nào ổn → tạo lại / thêm biến thể"; không hiện prompt gốc | Trung | `gate-pick-D` | Thêm nút phụ "Tạo lại 4 ảnh" (retry step gen với seed mới) và một dòng prompt gốc dưới tiêu đề |
| 7 | Cần duyệt | Lưới 3 cột cho 4 ảnh nên thừa một hàng; thumbnail ở dải phim bị cắt đáy | Trung | `gates-L`, `gate-pick-D` | `grid-template-columns: repeat(auto-fill, minmax(200px,1fr))`; dải phim dùng `object-fit: contain` |
| 8 | Toàn app | Lộ id kỹ thuật (`gen`, `gate.pick`, `op.*`, `comfy_node_errors`, `interrupted`) | **Cao** | `run-fail-list-L`, `batch-detail-L` | Ẩn mặc định. Đưa vào disclosure "Chi tiết kỹ thuật" hoặc tooltip. Cột bảng hàng loạt dùng tên step |
| 9 | Toàn app | Từ vựng: "Recipe", "run", "Huỷ run", "71 run", "Mã recipe", hướng `down_right` | Trung | `recipes-L`, `runs-L` | "Recipe" → **"Quy trình"**; "run" → **"lượt"**; hướng ghi **"Phải / Dưới-phải…"** hoặc S/SW giống Cài đặt. Xem bảng từ vựng ở §4.7 |
| 10 | Sidebar | 4 badge, 3 màu, nghĩa không khớp với nội dung màn | Trung | `dashboard-L`, `empty-runs-filter-L` | Chỉ giữ badge **Cần duyệt** (màu human). "Lượt chạy" dùng chấm spinner nhỏ khi có run *đang chạy thật*. Bỏ số ở Thư viện và Hàng loạt |
| 11 | Tổng quan | Mục "Đang chạy" lặp banner chờ duyệt; thẻ tài nguyên ít thông tin (Blender chỉ "4.5.3"); dải chấm step không có chú thích | Trung | `dashboard-L`, `dashboard-1600` | Gộp thành "Cần bạn" (gate, có thumbnail) và "Đang chạy" (chỉ run running/waiting, có progress). Thẻ tài nguyên gọn thành 1 dải. Chấm step có tooltip tên step |
| 12 | Tổng quan / lần đầu mở | Không có onboarding. Người mới không biết bắt đầu từ đâu, không kiểm tool path | **Cao** | `dashboard-L` | Hiện thẻ "Bắt đầu" khi chưa có run: 3 bước (Kiểm tra công cụ → Chọn quy trình → Tạo asset đầu tiên), mỗi bước có trạng thái ✓. Dùng art `emptyRuns` |
| 13 | Tạo mới | Tiêu đề recipe 26px lấn tiêu đề trang; danh sách recipe chỉ là list chữ dài, mô tả bị cắt "…" | Trung | `new-L`, `new-1600` | Recipe picker dạng lưới thẻ có art lớn, nhóm theo **2D / 3D / Nhân vật**. Tiêu đề nội dung tối đa 22px |
| 14 | Tạo mới / Hàng loạt | Hai form trùng tham số nhưng bố cục khác (Hàng loạt dùng native `<select>` và căn giữa); placeholder của textarea trông như nội dung thật; nút "Tạo lượt chạy" mờ khi chưa có prompt mà không giải thích | Trung | `batch-L` | Gộp: Tạo mới có toggle **"Một / Nhiều prompt"**, dùng chung `ParamField`. Placeholder dùng "Ví dụ: …" màu `--text-3`. Nút disabled kèm câu "Nhập ít nhất 1 prompt" |
| 15 | Run detail (lỗi) | Lỗi lặp 3 lần, 2 nút "Thử lại", không gợi ý sửa | **Cao** | `run-fail-list-L`, `run-interrupted-L` | Một callout duy nhất ở inspector: nguyên nhân bằng lời thường + **hành động gợi ý** (OOM → "Tắt Blender" / "Giải phóng VRAM"; interrupted → "Chạy tiếp") + "Thử lại". Card step chỉ giữ 1 dòng tóm tắt |
| 16 | Run detail (bị gián đoạn) | Đường nối giữa các step "Bỏ qua" vẫn màu xanh ok | Thấp | `run-interrupted-L` | Màu connector lấy theo trạng thái của step *đích* |
| 17 | Run detail (đang chạy) | Không có tiến độ tổng (bước 1/7, đã chạy, ETA ước tính), không báo ra ngoài app | Trung | `running-1` | Dải tiến độ mảnh dưới header (segment theo step). ETA ghi "ước tính" khi có lịch sử (§6). Electron `win.setProgressBar` + Notification khi tới gate/xong |
| 18 | Hàng loạt (chi tiết) | 4 thẻ công đoạn không phủ hết chiều rộng; "1 chờ bạn duyệt" không bấm được; "3 đang chạy" gộp luôn các lượt đang chờ GPU (đồng hồ cát) | Trung | `batch-detail-L` | Thẻ stat bấm được (lọc bảng / mở Cần duyệt), thêm số "chờ GPU". Thêm nút **"Duyệt cả lô"**: duyệt tuần tự các gate của batch |
| 19 | Thư viện | Lẫn file trung gian (JSON, Atlas) với asset; một asset (Hiệp sĩ Spine) chiếm 4 thẻ; nút ✓/✗ nổi đè lên ảnh | Trung | `gallery-L` | Gom theo **asset** (1 thẻ/asset, badge "+3 file"). Mặc định ẩn json/atlas. Nút duyệt chỉ hiện khi hover/focus, đặt dưới caption. Thêm tìm kiếm + lọc theo loại (2D/3D/Sprite/Spine) |
| 20 | Lightbox | Tên file vô nghĩa ("1074.png"), không có tên asset/prompt, không có hành động (duyệt, xuất, mở run), mũi tên trước/sau không hiện | Trung | `lightbox-img-D`, `lightbox-sprite-L` | Footer: tên asset + recipe + thời gian · Duyệt/Từ chối · Xuất · Mở lượt chạy. Mũi tên ‹ › luôn hiện, phím ←/→ |
| 21 | Recipe list | Badge "Gốc" ở mọi thẻ (không mang thông tin); Spine thiếu art; mô tả dạng chuỗi mũi tên | Thấp | `recipes-L` | Chỉ gắn badge cho recipe *người dùng tạo* ("Của tôi"). Sinh art Spine bằng Codex. Mô tả 1 câu + lane tags |
| 22 | Recipe editor | Form inspector lộ cú pháp (`${inputs.ten}`), select native; minimap hỏng | Thấp | `recipe-editor-L` | Đây là chế độ nâng cao nên chấp nhận được. Chỉ sửa minimap/fit và thay select native bằng popup kiểu HIG |
| 23 | Cài đặt | Tiêu đề nội dung 26px; phụ đề menu dùng mono; ô đường dẫn không có nút "Chọn…" | Trung | `settings-L`, `settings-tools-L` | Nút "Chọn…" (`dialog.showOpenDialog`). Phụ đề menu dùng font thường. Tách "Preset xuất" thành mục riêng, dễ đọc hơn |
| 24 | Core offline / banner | Offline chỉ có "Copy nhật ký"; banner đẩy toolbar xuống | **Cao** | `offline-D`, `banner-restart-L` | Offline: **Khởi động lại core** · **Mở Cài đặt → Đường dẫn** · Copy nhật ký. Banner nằm *dưới* toolbar (overlay hoặc trong `.view`), không đẩy vùng kéo/caption |
| 25 | Empty states | Có art nhưng không có CTA ("Không có run nào ở mục này") | Thấp | `empty-runs-filter-L` | Mỗi empty state có 1 hành động: "Tạo asset mới", "Xem tất cả" |
| 26 | Bàn phím | Có Ctrl+1…7 / Ctrl+N / phím ở Gates nhưng không ai biết; chưa có command palette | Trung | — | **Ctrl+K** palette (điều hướng, tạo theo recipe, mở run gần đây). Tooltip sidebar hiện phím tắt. Cheat-sheet bằng phím `?` |
| 27 | Tương phản | `--st-pending #8e8e93` trên nền sáng = **3.15:1** (dưới AA khi dùng làm chữ). Chữ 10–11px ở node graph | Trung | `run-awaiting-L` | Thêm `--st-pending-text: #6e6e75` (≈4.9:1). Chữ tối thiểu 11px, nhãn node ≥ 12px |
| 28 | Run detail header | Nút "Huỷ run" ngang hàng toggle view, dễ bấm nhầm; không xác nhận? | Trung | `running-1` | Đưa vào menu "…" hoặc tách xa, dùng màu destructive, có confirm hoặc undo toast |

Các tỷ lệ tương phản đã đo (đạt AA): text-3/nền 4.88, accent link 5.22 (sáng) / 6.03 (tối), chữ trên accent 5.39 / 6.6,
waiting 4.52, human-on-soft ≈ 4.4 (sát ngưỡng, nên đậm hơn 1 bậc).

---

## 3. Đề xuất nâng cấp theo đợt

> Đang có agent khác sửa `App.vue`, `AppSidebar.vue`, `GatesView.vue`, `NewRunView.vue`, `RecipeEditorView.vue`,
> `RunDetailView.vue` (resizable panes). Đợt 1 nên bắt đầu **sau khi** phần đó merge, hoặc chỉ làm các mục không đụng
> những file này trước (đánh dấu ◇).

### Đợt 1: nhanh, hiệu quả cao (~2–3 ngày)

| Mục | Phạm vi file | Công | Rủi ro |
|-----|--------------|------|--------|
| Graph fit + bỏ/sửa minimap + mặc định Danh sách khi hẹp (#2) | `components/flow/FlowCanvas.vue`, `RunDetailView.vue`, `RecipeEditorView.vue` | 0.5 ngày | Thấp. Phải phối hợp với ResizeHandle (fit lại khi pane đổi) |
| Ẩn id kỹ thuật + sửa từ vựng (#8, #9) | `composables/format.ts` (map tên), `StepChip.vue`, `StepNode.vue`, `BatchDetailView.vue`, `RunsView.vue`, copy rải rác | 0.5 ngày | Thấp ◇ (phần lớn) |
| Thumbnail trong dòng run/gate (#4) | `RunRowItem.vue`, `GatesView.vue` (list trái), store `runs` (lấy hash output cuối) | 0.5 ngày | Trung: cần core trả `preview_hash` cho run. Nếu chưa có thì lấy output step cuối |
| Badge sidebar gọn (#10), sửa mâu thuẫn số đếm | `AppSidebar.vue`, `stores/runs.ts` | 2 giờ | Thấp |
| Callout lỗi gộp + gợi ý sửa (#15), connector skipped (#16) | `RunDetailView.vue`, `StepChip.vue`, `composables/format.ts` (map error code → lời khuyên) | 0.5 ngày | Thấp |
| Core offline có nút hành động, banner không đẩy toolbar (#24) | `CoreOffline.vue`, `CoreBanner.vue`, `App.vue`, IPC `core:restart` (đã có supervisor) | 0.5 ngày | Trung (IPC) ◇ một phần |
| Token: chữ pending, cỡ chữ tối thiểu, tiêu đề tối đa 22px (#7, #27) | `styles/tokens.css`, `StepNode.vue`, `NewRunView.vue`, `SettingsView.vue`, `BatchDetailView.vue` | 2 giờ | Thấp ◇ |

### Đợt 2: cấu trúc / flow (~5–7 ngày)

| Mục | Phạm vi file | Công | Rủi ro |
|-----|--------------|------|--------|
| **Xuất asset**: Xuất… / Hiện trong thư mục / Copy path / kéo ra ngoài (#1) | `src/main/index.ts` (IPC + whitelist), `preload`, component mới `AssetActions.vue` dùng ở run detail, Thư viện, Lightbox. Core: endpoint export theo preset | 1.5–2 ngày | Trung: bảo mật đường dẫn (chỉ blob trong data dir), preset sprite đã có |
| **Result hero** cho run hoàn tất (#3) + duyệt inline trong run (#5) | `RunDetailView.vue`, tách `GatePicker.vue`/`GateApprove.vue` từ `GatesView.vue` | 1.5 ngày | Trung: tách component đụng file đang bị sửa |
| Onboarding "Bắt đầu" + Tổng quan tái cấu trúc (#11, #12) | `DashboardView.vue`, `ResourceCard.vue`, component `GettingStarted.vue`, đọc trạng thái tool path | 1 ngày | Thấp |
| Gộp Tạo mới + Hàng loạt (toggle Một/Nhiều) + recipe picker dạng lưới (#13, #14) | `NewRunView.vue`, `BatchView.vue` (giữ route `/batch/:id`), sidebar bớt 1 mục | 1.5 ngày | Trung: đổi IA, cần redirect `/batch` → `/new?mode=batch` |
| Thư viện gom theo asset + lọc loại + tìm kiếm; lightbox có hành động (#19, #20) | `GalleryView.vue`, `Lightbox.vue`, `stores/versions.ts` | 1.5 ngày | Trung: virtual scroll + group |
| Command palette Ctrl+K + tooltip phím tắt (#26) | component `CommandPalette.vue`, `App.vue` | 1 ngày | Thấp |

### Đợt 3: polish / motion (~3–4 ngày)

| Mục | Phạm vi file | Công | Rủi ro |
|-----|--------------|------|--------|
| Tiến độ tổng của run (segment bar) + ETA "ước tính" + taskbar progress + Notification (#17) | `RunDetailView.vue`, `RunRowItem.vue`, main `win.setProgressBar`, `Notification` | 1 ngày | Thấp. ETA chỉ hiện khi có lịch sử (PLAN cấm giả tiến độ) |
| Motion theo §6: check morph khi xong, shake nhẹ khi lỗi, shared-element thumbnail → lightbox / run → result hero (motion-v `layoutId`), stagger 30ms cho list | `StatusGlyph.vue`, `StepChip.vue`, `Lightbox.vue`, `composables/motion.ts` | 1.5 ngày | Thấp. Phải giữ `reduced-motion` |
| Duyệt cả lô + stat bấm được ở Hàng loạt (#18) | `BatchDetailView.vue`, `stores/gates.ts` | 0.5 ngày | Thấp |
| Cài đặt: nút "Chọn…", tách Preset xuất, sửa font phụ đề (#23) | `SettingsView.vue`, `ToolPathsPanel.vue`, IPC dialog | 0.5 ngày | Thấp |
| Art còn thiếu (Spine recipe, onboarding) bằng Codex image qua adapter | `assets/art/*` | 0.5 ngày | Thấp |

---

## 4. Design direction

**Định hướng: "Studio bàn đèn".** Vẫn theo macOS/HIG: chrome yên lặng, Inter, accent tím. Cái riêng (signature) của
app là **asset luôn đặt trên nền checker, dùng làm điểm nhấn chính**: mọi danh sách đều có thumbnail checker, run
xong có result hero lớn, lightbox mở bằng shared-element. Chrome thì trung tính, màu dành cho asset và trạng thái.
Không thêm gradient trang trí.

### 4.1 Màu / token (giữ phần lớn, chỉ bổ sung)

| Token | Sáng | Tối | Ghi chú |
|-------|------|-----|---------|
| `--accent` | `#6e56cf` | `#9e8cfc` | giữ |
| `--bg-content` | `#fbfbfd` | `#1e1e21` | giữ |
| `--bg-stage` (mới) | `#f2f2f6` + checker | `#18181a` + checker | nền viewer/result hero |
| `--st-pending-text` (mới) | `#6e6e75` (4.9:1) | `#a1a1a8` | dùng cho *chữ*, giữ `--st-pending` cho chấm |
| `--st-human` | `#b83000` (đậm hơn 1 bậc) | `#ff8a4c` | human-on-soft ≥ 4.5 |
| `--danger` (alias) | `= --st-fail` | | nút destructive (Huỷ, Từ chối) |
| `--scrim` (mới) | `rgba(0,0,0,.40)` | `rgba(0,0,0,.55)` | lightbox/modal |

Quy tắc: badge đỏ/cam **chỉ** cho việc cần người dùng làm; xanh lá chỉ cho "xong"; tím cho đang chạy/chọn.

### 4.2 Type scale (HIG macOS, Inter)

| Vai | Cỡ / weight / line-height | Dùng cho |
|-----|---------------------------|----------|
| Large title | 26 / 700 / 32 | **chỉ** màn rỗng/onboarding |
| Title 1 | 22 / 600 / 28 | tiêu đề nội dung (recipe đang chọn, tên preset) |
| Title 2 | 17 / 600 / 22 | tiêu đề toolbar trang (giữ) |
| Headline | 13 / 600 / 18 | tên dòng, tên step |
| Body | 13 / 400 / 18 | mặc định |
| Callout | 12 / 400 / 16 | meta, mô tả |
| Caption | 11 / 500 / 14 | badge, nhãn phụ (tối thiểu) |
| Mono | Cascadia Mono 12 | **chỉ** đường dẫn, lệnh, log |

Hiện dùng 10px ở `StepNode.vue:220`, `FlowCanvas.vue:167`, `SpineViewer.vue:213` → nâng lên 11.

### 4.3 Spacing / radius / elevation
- Spacing 4-pt đã có (`--sp-1…8`), thêm `--sp-10: 40px`, `--sp-12: 48px` cho khoảng giữa section. Gutter nội dung 24px,
  section cách nhau 32px, max-width nội dung 1200px (Tổng quan đang co 4 thẻ tài nguyên không đều ở 1600).
- Radius: giữ sm6 / md10 / lg14 / xl18. Thumbnail 8, card 14, sheet/lightbox 18.
- Elevation: `shadow-1` cho card, `shadow-2` cho popover/toolbar nổi trên viewer, `shadow-3` cho lightbox/palette.
  Không có tầng nào khác.

### 4.4 Component specs chính
- **Button**: cao 28 (toolbar) / 32 (form). Primary = nền accent; Secondary = elevated + hairline; Destructive = chữ
  `--danger`, tách xa primary ≥ 16px. Mọi trạng thái có `:focus-visible` ring 2px `--focus-ring` (base.css đã có).
- **Row (run/gate)**: `[thumb 40] tên · badge trạng thái │ meta (quy trình · thời gian) │ mini-progress │ ›`. Cao 56.
- **Status**: luôn là glyph + chữ (không chỉ màu). Chấm step có tooltip tên step + trạng thái.
- **Callout lỗi**: icon + câu nguyên nhân bằng lời thường + 1–2 nút hành động gợi ý + "Chi tiết kỹ thuật ▸".
- **Viewer stage** (GLB/Sprite/Spine/Ảnh): nền checker, toolbar nổi dạng pill ở đáy (đang làm tốt), khung info pill
  góc trên-trái (đang làm tốt), thêm nút Xuất ở góc trên-phải.

### 4.5 Mockup: Run detail (hoàn tất), đề xuất

```
┌───────────────────────────────────────────────────────────────────────────────────────────────┐
│ ←  Bệ pha lê                         Prop 3D · 2 phút 23 giây    [Danh sách|Sơ đồ]  ● Hoàn tất  ⋯ │
│ ████████████████████████████████████████████████████████████████████  7/7 bước                 │
├──────────────────────────────┬────────────────────────────────────────────────────────────────┤
│ ✓ Sinh ảnh concept     14 giây│  KẾT QUẢ                                                        │
│ ✓ Chọn ảnh  [thumb]    13 giây│  ┌───────────────────────── checker stage ──────────────────┐  │
│ ✓ Dựng mesh 3D         34 giây│  │ 184 tam giác · 552 đỉnh · 21 KB             [⤓ Xuất…]   │  │
│ ✓ Dọn mesh (Blender)   42 giây│  │                     (GLB viewer)                          │  │
│ ✓ Kiểm tra GLB          9 giây│  │             ( Tự xoay · Khung dây · Lưới · ⌖ )            │  │
│ ✓ Render các hướng     20 giây│  └───────────────────────────────────────────────────────────┘  │
│ ✓ Duyệt kết quả        12 giây│  [Hiện trong thư mục]  [Copy đường dẫn]  [✓ Đưa vào thư viện]   │
│                               │  ─────────────────────────────────────────────────────────────  │
│                               │  Bước đang chọn: Render các hướng ▸ (output, tham số)           │
└──────────────────────────────┴────────────────────────────────────────────────────────────────┘
```

### 4.6 Mockup: Tổng quan, đề xuất

```
┌ Tổng quan ─────────────────────────────────────────────────────────── [⌘K Tìm…]  [+ Tạo mới] ┐
│ ComfyUI ● 13,4/16 GB ▂▂▂▁  ·  Blender ○ tắt [Bật]  ·  Codex ● sẵn sàng  ·  Claude ● sẵn sàng   │
│                                                                                             │
│ CẦN BẠN (4)                                                               [Duyệt lần lượt →] │
│ [img] Bình thuốc tím     Chọn 1 trong 4 ảnh        vừa xong                                  │
│ [img] Kiếm pha lê xanh   Chọn 1 trong 4 ảnh        vừa xong                                  │
│ [glb] Bệ pha lê          Duyệt mesh 3D             5 phút                                    │
│                                                                                             │
│ ĐANG CHẠY (1)                                                                               │
│ [live] hiệp sĩ rồng lửa  Sinh ảnh · Lấy mẫu 4/9 ███████░░░░ 44%   bước 1/7 · ~2 phút ước tính │
│                                                                                             │
│ GẦN ĐÂY                                                                       Xem tất cả ›  │
│ [img][img][img][img][img][img]   ← lưới thumbnail asset vừa xong, bấm mở lightbox            │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
(Lần đầu, chưa có run: thay "CẦN BẠN" bằng thẻ BẮT ĐẦU: ✓ Công cụ · ○ Chọn quy trình · ○ Asset đầu tiên)
```

### 4.7 Từ vựng đề xuất

| Hiện tại | Đề xuất |
|----------|---------|
| Recipe / Recipe mới / Mã recipe | Quy trình / Quy trình mới / Mã (ẩn ở chế độ nâng cao) |
| run, 71 run, Huỷ run, Xem run | lượt chạy, 71 lượt, Huỷ lượt chạy, Mở lượt chạy |
| Gốc (badge) | (bỏ) · "Của tôi" cho quy trình tự tạo |
| `gen` / `pick` / `rmbg` / `fit` | Sinh ảnh / Chọn ảnh / Tách nền / Căn cỡ (id vào tooltip) |
| `comfy_node_errors`, `interrupted` | "Lỗi trong ComfyUI", "Bị dừng giữa chừng" |
| right, down_right… | Phải, Dưới-phải… (hoặc E, SE… giống Cài đặt) |
| Đầu vào · images / Đầu ra · meshes | Ảnh đầu vào / Mesh đầu ra |

---

## 5. Những gì KHÔNG nên đổi (đang tốt)

- **Hệ token** (`tokens.css`): màu chỉ khai báo trong token, light/dark song song, tương phản đạt AA, lane color theo
  tài nguyên (GPU/Blender/CPU/Cloud) rõ và nhất quán.
- **Viewer**: GLB (stats pill + toolbar nổi), Sprite (fps, zoom pixel, pivot, dải 8 hướng), Spine (anim switch, ghi
  chú license). Chất lượng cao, giữ nguyên ngôn ngữ thiết kế này cho viewer mới.
- **Gates**: phím 1–9 / ←→ / Enter / X có hint bằng `kbd`, nút Duyệt/Từ chối ở footer cố định.
- **Running state**: preview live trong lúc sampling, "Lấy mẫu 4/9 · 44% · node KSampler", đồng hồ tabular-nums,
  đúng §6.
- **List view của run** (timeline dọc, chip lane, "Xong sau 14 giây"): dễ đọc, nên làm mặc định.
- Art Codex cho empty state / recipe / core-disconnected: cùng một phong cách 3D tím, đẹp. Chỉ cần bổ sung art còn thiếu.
- Sidebar resize/rail, Ctrl+1…7, `MotionConfig reduced-motion="user"`, transition trang 240ms spring.
- Diagnostics panel (copy/xuất log, bước lỗi gần đây) và thẻ "Kết nối agent CLI": rõ ràng, đúng việc.

---

## 6. Ghi chú kỹ thuật phát sinh khi khảo sát

- Log renderer có nhiều lỗi `<pattern>/<circle> attribute …: Expected length, "NaN"` từ vue-flow (Background/MiniMap),
  khả năng do canvas có kích thước 0 lúc mount (pane đang resize / view ẩn). Nên guard trước khi render minimap.
- `RecipeEditorView.vue:343-346`: leave-guard dùng `window.confirm` native (chặn renderer, hộp thoại không theo HIG),
  và hiện ngay cả khi vừa mở `/recipes/new` chưa sửa gì (doc mới bị tính là `dirty`). Nên dùng sheet xác nhận trong
  app và chỉ hỏi khi có thay đổi thật.
- Codex: Tổng quan báo "Sẵn sàng" trong khi Cài đặt → Đường dẫn báo "Không tìm thấy file" (mock). Nên dùng chung một nguồn trạng thái.
