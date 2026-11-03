## WP-17C — DONE
Đã đổi: core export/shader_preview.py (sprite_from_file: >8 MB / >16M px kiểm header trước decode / format whitelist PNG-JPEG-WEBP-GIF-BMP / trong suốt hoàn toàn -> SampleError; fit 88 px + đệm; cache key thêm hash), api/library.py (blob=<hash>: blobs.path regex -> 400 invalid_hash, thiếu -> 404, >8 MB hoặc không phải ảnh -> 400), tests/test_shader_preview.py (+3 test). App: composables/shaderPicker.ts (shaderPreviewUrl nhận sampleHash, chỉ gửi nếu khớp 64 hex), ShaderPicker.vue (prop sampleHash, reset lỗi khi đổi ảnh), BundleMenu.vue (prop sampleHash -> picker), AssetActions.vue (+1 computed, +1 binding: truyền hash của ảnh), PackDialog.vue (+computed shaderSample ở trước hasShaderEngine, +binding :sample-hash trên <ShaderPicker> dòng ~285).
Tiêu chí: đạt. Không có ảnh -> sprite slime như cũ.
Test: pytest shader_preview+library_import+shaders 27 pass. Typecheck: chỉ lỗi mock/server.ts:1925 (PackCandidate thiếu engines, của 17A), file của 17C sạch.
Nhìn: docs/qa/r17/WP-17C/01_default_vs_asset_sample.png (6 preset, slime vs ảnh tuỳ biến, qua route thật). Không chụp UI Electron/vite.
Hot file cần thêm: không bắt buộc (mock không có ảnh preview như 16B).
Commit message đề xuất: feat(shaders): preview presets on the asset's own image (GET /api/shaders/{id}/preview?blob=<hash>, size and pixel limits) and pass it from the export menu and pack dialog
