## WP-19C: So sánh 2 lượt chạy
- Mục tiêu: RunsView (Lịch sử) cho chọn 2 run (checkbox/Shift-click, thanh chọn "So sánh 2 lượt") và RunDetailView có "So sánh với…" (chọn run khác cùng recipe gần đây) → `RunDiff.vue` (dialog hoặc route /runs/compare?a=&b= — thêm route vào router.ts nếu chọn route). Nội dung: recipe, trạng thái, thời gian chạy, chi phí, seed_used từng step, inputs (blob: thumbnail), params — mặc định chỉ hiện dòng khác, công tắc "Hiện tất cả"; output ảnh chính hai bên → nút mở CompareView có sẵn (components/CompareView.vue, WP-18C). Khác recipe vẫn cho so nhưng cảnh báo.
- Dữ liệu: api().getRun / steps có sẵn trong client (không thêm endpoint; cần thì ghi "Hot file cần thêm").
- Phạm vi: app views/RunsView.vue, RunDetailView.vue, components/RunDiff.vue (mới), components/RunRowItem.vue nếu cần checkbox, router.ts (nếu route). Cấm hot file, sendTo.ts, CommandPalette, LibraryImportDialog.
- Model: sonnet. Không build. typecheck. Ảnh docs/qa/r19/WP-19C/.
- Báo cáo: `.planning/wp/WP-19C-report.md`
## Nhật ký
- RunDiff.vue (dialog) viết xong: bảng khác biệt, Hiện tất cả, cảnh báo khác recipe/đang chạy, nút mở CompareView.
- RunsView: checkbox + Shift-click, pickbar, mở RunDiff; RunRowItem prop selectable/selected.
- RunDetailView: menu "So sánh với…" (8 run gần nhất cùng recipe) mở RunDiff. typecheck: chỉ còn lỗi mock/server.ts (WP khác).
- Chụp bằng Edge headless + CDP (extension Chrome mất kết nối) vào docs/qa/r19/WP-19C/01..10. Vite tạm cổng 5192 đã tắt theo PID, file cấu hình tạm đã xoá.
