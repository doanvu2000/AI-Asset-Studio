## WP-18C: So sánh 2 phiên bản
- Mục tiêu: component mới `CompareView.vue` (overlay toàn màn, focus trap): 3 chế độ — slider kéo ngang (mặc định), cạnh nhau, nháy (A/B đổi mỗi 600 ms, tôn trọng prefers-reduced-motion → tắt nháy tự động, đổi bằng phím Space). Zoom đồng bộ (wheel), pan đồng bộ, nền ô caro cho alpha, hiện tên/kích thước/dung lượng/QA score mỗi bản. Phím: ←→ di slider 5%, 1/2/3 đổi chế độ, Esc đóng.
- Lối vào: Thư viện (GalleryView selbar) khi chọn đúng 2 → nút "So sánh"; Lightbox phím C so sánh ảnh đang xem với ảnh trước đó trong siblings; RunDetail 2 output ảnh (nếu gọn).
- Phạm vi: app components/CompareView.vue (mới), GalleryView.vue, Lightbox.vue, RunDetailView.vue (tuỳ). Cấm hot file (types/client/mock) — dùng dữ liệu đã có; cấm sendTo.ts/SendToMenu/CommandPalette (18B).
- Model: sonnet. Không build. typecheck. Ảnh docs/qa/r18/WP-18C/ (mock hoặc Electron thật) từng chế độ.
- Báo cáo: `.planning/wp/WP-18C-report.md`
## Nhật ký
- CompareView.vue: viết lại thêm 3 chế độ (trượt/cạnh nhau/nháy), zoom+pan đồng bộ, caro, QA; Gallery truyền qa.
- Lightbox: phím C / nút So sánh với ảnh trước trong ring (ẩn nếu không phải ảnh); RunDetail bỏ qua (dùng Lightbox sẵn có). typecheck sạch.
- Chụp mock: 01_slide, 02_side, 03_blink (docs/qa/r18/WP-18C).
- Xong, report ghi.
