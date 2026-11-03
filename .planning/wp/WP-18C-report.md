## WP-18C — DONE (RunDetail bỏ qua)
Đã đổi: components/CompareView.vue (3 chế độ trượt/cạnh nhau/nháy, zoom wheel + pan đồng bộ, nền caro, tên/kích thước/dung lượng/QA mỗi bản, phím ←→ 5%, 1/2/3, Space, 0, Esc; ảnh không phải image thì ẩn nút chế độ, rơi về AssetViewer cạnh nhau + duyệt như cũ); GalleryView.vue (truyền qa); Lightbox.vue (nút/phím C so sánh với ảnh trước trong siblings, ẩn nếu không phải ảnh).
Tiêu chí: slider mặc định OK; nháy 600ms, reduced-motion tắt tự nháy (nút Nháy disabled, Space đổi A/B); ảnh 2 kích thước dùng chung scale (fit theo ảnh lớn); focus trap tái dùng.
Test: npm run typecheck sạch. Mock vite cổng 5191 (đã tắt theo PID).
Nhìn: docs/qa/r18/WP-18C/01_slide, 02_side, 03_blink, 04_non_image_fallback (.jpg). Mock chỉ có ảnh cùng 256x256 nên chưa nhìn được cặp khác kích thước (logic scale chung đã review). Lightbox C chưa chụp.
Hot file cần thêm: không.
Commit đề xuất: feat(compare): slider / side-by-side / blink modes with synced zoom and pan, QA and file info, Lightbox C shortcut
