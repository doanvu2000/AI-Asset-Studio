## WP-16B: Xem trước shader động
- Mục tiêu: người chọn shader thấy hiệu ứng trước khi xuất.
  - Core: render CPU (numpy, tái dùng golden/CPU port nếu có trong test hoặc export — grep "golden" trong core/tests cho flutter shader) mỗi preset trong export/shaders.catalog() trên 1 sprite mẫu nhúng sẵn (vẽ bằng PIL, không cần file ngoài) → webp động ≤ 96 px, ≤ 24 khung, cache trên đĩa theo (id, params hash). GET /api/shaders/{id}/preview?color=&speed= trả image/webp. Không có port CPU cho preset nào → trả ảnh tĩnh + header báo.
  - App: ShaderPicker.vue hiện thumbnail (img src qua client URL helper có token như ảnh blob hiện có) cạnh mỗi shader; animate chỉ khi hover/focus (ảnh tĩnh khung 0 khi không), tôn trọng prefers-reduced-motion.
- Phạm vi: core export/shader_preview.py (mới), route trong api/library.py (nơi có GET /api/shaders), test; app components/ShaderPicker.vue, composables/shaderPicker.ts. Hot file client.ts/types.ts/mock → ghi "Hot file cần thêm" (main gộp).
- Model: sonnet. Không build. pytest + typecheck.
- Nghiệm thu: test render mỗi preset ra webp nhiều khung, khung khác nhau, ≤ 200 ms/preset sau cache lạnh trên CPU (đo, ghi số); typecheck; ảnh UI (vite mock hoặc Electron thật) docs/qa/r16/WP-16B/.
- Nhìn thấy được: có.
- Báo cáo: `.planning/wp/WP-16B-report.md`
## Nhật ký
- 13:42 AUDIT xong: không có CPU port, tự viết numpy từ .gdshader; token qua ?t=
- 13:43 core: shader_preview.py + route /api/shaders/{id}/preview (token ?t=) xong
- 13:44 test_shader_preview.py viết, chạy
- 13:46 thêm ?still=1 (khung tĩnh cho thumbnail), tối ưu encode: 11-73 ms/preset
- 13:47 app: ShaderPicker thumbnails (window.studio.getConnection, không cần hot file) viết xong
- 13:49 DONE, report ghi
