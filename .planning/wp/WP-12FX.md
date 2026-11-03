## WP-12FX: Gallery bộ lọc 1 hàng + Flow editor Delete/Undo + phím tắt (audit U3, U1, U5)
- Mục tiêu:
  1. GalleryView (440-500): bộ lọc 3 hàng → 1 hàng: ô tìm + chip "Lọc (n)" mở popover (loại/trạng thái/thấp điểm/sắp xếp, giữ role=radiogroup), nút xoá lọc. Lưu bộ lọc trong URL query như hiện có (nếu có).
  2. FlowCanvas/FlowEditorView: Delete/Backspace xoá node/dây đang chọn (confirm nếu node có dây; bỏ qua khi focus ở input), Ctrl+Z / Ctrl+Y (Ctrl+Shift+Z) undo/redo cho thêm/xoá/nối/di chuyển node, stack ≤ 50, xoá stack khi load flow khác. Không phá Ctrl+S, chặn rời khi chưa lưu.
  3. Ctrl+Enter chạy ở FlowStartView; Ctrl+Enter quét ở AuditView/RestyleView.
- Phạm vi: app/src/renderer/src/views/GalleryView.vue, components/flow/FlowCanvas.vue, views/FlowEditorView.vue, views/FlowStartView.vue, views/AuditView.vue + RestyleView.vue (chỉ phím tắt), components/CommandPalette (nếu có liệt kê phím tắt) + composable mới nếu cần (vd composables/undoStack.ts). | Cấm: api/types.ts, api/client.ts, RunDetailView.vue, StepInspector.vue, api/mock/server.ts (WP-12B), core/**.
- Skill: web-design. Model: sonnet.
- Tiêu chí:
  - [ ] typecheck sạch; unit test undoStack nếu repo có vitest (kiểm `app/package.json`), không có thì test thủ công qua Playwright.
  - [ ] Playwright mock: Gallery bộ lọc 1 hàng + popover mở (light+dark, 1280); Flow editor: thêm node → Delete → Ctrl+Z khôi phục (ảnh trước/sau) → docs/qa/r12/WP-12FX/.
  - [ ] Không cuộn ngang 1280/1600.
- Nhìn thấy được: có.
- Báo cáo: `.planning/wp/WP-12FX-report.md`
## Nhật ký
- bước1: undoStack.ts + FlowEditor Delete/undo/redo; Gallery bộ lọc 1 hàng + popover; typecheck sạch
- bước2: Ctrl+Enter FlowStart/Audit/Restyle + palette; typecheck sạch
- bước3: QA Playwright xong, báo cáo ghi
