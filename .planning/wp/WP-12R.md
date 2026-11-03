## WP-12R: README + What's new cho tính năng lượt 12 (audit W7)
- Mục tiêu: README.md (giữ dạng ngắn liệt kê như bản 1.4.0, 80 dòng ± 20) bổ sung: theme UI từ style bible; normal map AI (cài trong Cài đặt); xuất Compose/SwiftUI cho tile_anim/autotile/vfx/weather/ui_screen/minimap/card_set; tự xuất khi duyệt; chạy lại giữ/đổi seed; thiết lập đã lưu; 1 prompt × N seed; tự chọn ảnh QA cao nhất; bộ lọc Gallery, Delete/Undo flow; test nhanh (BUILD.md). Mục "Dùng lần đầu": đường project → Flutter (engine đích + tự xuất khi duyệt), phím tắt Ctrl+Enter/Ctrl+K.
- Thêm mục "Chưa phát hành" (hoặc 1.5.0-dev — xem cách file changelog.ts đặt) đầu `app/src/renderer/src/composables/changelog.ts` liệt kê ngắn các mục trên. KHÔNG bump version.
- Phạm vi: README.md, app/src/renderer/src/composables/changelog.ts. | Cấm: mọi file khác.
- Model: sonnet. Tiếng Việt có dấu, văn ngắn.
- Tiêu chí: typecheck sạch (changelog.ts); test_version pass; README không link chết (đường dẫn file trong repo tồn tại).
- Báo cáo: `.planning/wp/WP-12R-report.md`
## Nhật ký
