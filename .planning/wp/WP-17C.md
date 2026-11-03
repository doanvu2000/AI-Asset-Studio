## WP-17C: Xem trước shader trên ảnh của asset
- Core: GET /api/shaders/{id}/preview nhận `blob=<hash>` (ảnh trong blob store; resize ≤ 96 px giữ alpha; >8 MB hoặc không phải ảnh → 400; cache key thêm hash). export/shader_preview.py nhận sprite tuỳ biến. Test.
- App: ShaderPicker nhận prop `sampleHash?`; BundleMenu truyền ảnh của step (preview hash có sẵn trong options/step outputs), PackDialog truyền preview_hash của asset đầu tiên được chọn (listing pack đã có preview_hash). Không có → sprite mẫu như cũ.
- Phạm vi: core export/shader_preview.py, api/library.py, tests/test_shader_preview.py; app components/ShaderPicker.vue, composables/shaderPicker.ts, BundleMenu.vue, PackDialog.vue (chỉ truyền prop — 17A cũng sửa PackDialog: CHỈ thêm 1 prop binding, báo cáo dòng đã sửa để main gộp nếu xung đột).
- Model: sonnet. Không build. pytest + typecheck. Ảnh docs/qa/r17/WP-17C/.
- Báo cáo: `.planning/wp/WP-17C-report.md`
## Nhật ký
- AUDIT: blobs.path() có _HASH_RE 64 hex; route cũ chỉ preset mẫu.
- CORE xong: sprite_from_file (8MB, 16M px kiểm header, whitelist format, fit 88 px + đệm), blob= trên route, cache key + hash. 
- APP xong: shaderPreviewUrl(sampleHash), ShaderPicker/BundleMenu/PackDialog/AssetActions truyền prop.
- TEST: pytest 27 pass; typecheck chỉ còn lỗi mock/server.ts:1925 (của 17A). NHÌN: docs/qa/r17/WP-17C/01_*.png. DONE.
