## WP-13A: Chạy lại ở RunsView + Delete dây trong Flow + bàn phím StepNode
- Mục tiêu:
  1. RunsView/RunRowItem: menu hàng (hoặc nút ⋯) có "Chạy lại, seed mới" / "Chạy lại, giữ seed" (dùng composables/rerun.ts + api().rerunRun, commit 56a6ca2); chỉ với run đã kết thúc; giữ seed xám khi run không có seed_used (như RunDetailView).
  2. Flow editor: bấm dây → chọn dây (viền nổi), Delete/Backspace xoá dây đang chọn, đi qua undo stack (composables/undoStack.ts); Esc bỏ chọn. Thay hành vi "bấm dây để gỡ" hiện có bằng chọn+Delete (hoặc giữ nút gỡ nhỏ trên dây đã chọn). Confirm khi xoá node có dây (đã có — kiểm chạy thật).
  3. StepNode trong FlowCanvas (RecipeEditor + FlowEditor): tabindex=0, Enter chọn node, mũi tên chuyển node lân cận theo vị trí, focus-visible theo token có sẵn (audit U6).
- Phạm vi: app/src/renderer/src/views/RunsView.vue, components/RunRowItem.vue (hoặc tên tương ứng), components/flow/FlowCanvas.vue, components/flow/StepNode.vue, views/FlowEditorView.vue, views/RecipeEditorView.vue (chỉ nếu cần nối focus), composables/undoStack.ts. | Cấm: core/**, api/types.ts, api/client.ts, api/mock/server.ts (rerun mock đã có), AuditView/RestyleView/GalleryView (WP-13C).
- Skill: web-design. Model: sonnet.
- Tiêu chí: typecheck sạch; Playwright mock: menu Chạy lại ở RunsView (light+dark), chọn dây + Delete + Ctrl+Z khôi phục, Tab/mũi tên giữa node — ảnh vào docs/qa/r13/WP-13A/; không cuộn ngang 1280.
- Nhìn thấy được: có. Báo cáo: `.planning/wp/WP-13A-report.md`
## Nhật ký
- RunRowItem: nút ⋯ + menu Teleport (Chạy lại seed mới/giữ seed), RunsView bật prop menu
- FlowEditorView: chọn dây (picked) + Delete/Esc + thanh Gỡ dây; FlowCanvas: Enter/Space/mũi tên giữa node, focus ring, disable-keyboard-a11y
- Playwright runs menu OK (light+dark), tiếp flow
- Xong, báo cáo WP-13A-report.md
