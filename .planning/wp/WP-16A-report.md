## WP-16A — DONE
Đã đổi: core/studio_core/store/bundle.py (still_images, _source_ready, engine_ready(step, blobs, engine, stills), export_engine(stills=) — ảnh PNG/JPG/WebP đơn map thành ui borderless cho android/ios, tái dùng density/imageset/Kotlin/Swift sẵn có); store/pack.py (truyền version blob, engine_ready theo engine); core/tests/test_pack_mobile.py.
Tiêu chí 4/4: ảnh 256/1024 đủ 5 density android (mdpi..xxxhdpi, xxxhdpi=gốc) và 1x/2x/3x iOS; tên [a-z][a-z0-9_]*; Contents.json hợp lệ; pack project chỉ PNG ra file cho android+ios; glb vẫn skip.
Test: pytest xdist full 1838 passed, 6 skipped, 6 failed — đều test_shader_preview.py (agent khác đang sửa shader_preview), không thuộc WP này. test_pack_mobile + test_pack pass.
Lưu ý: pack luôn thêm prefix folder nên tên Android là icon_icon; flutter giữ nguyên skip.
Hot file cần thêm: api/routes.py:585 và api/pack.py:140 gọi engine_ready(step, blobs) không truyền engine => chưa liệt kê android/ios cho ảnh tĩnh ở export menu/preview; cần truyền engine (api/routes.py:621 export_engine trực tiếp đã hoạt động).
Commit message đề xuất: feat(export): android and ios export of single still images (density drawables, imagesets) and pack no longer skips them
