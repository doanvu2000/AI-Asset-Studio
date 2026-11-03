## WP-16B — DONE
Đã đổi: core/studio_core/export/shader_preview.py (mới, numpy port 6 preset từ .gdshader, sprite slime vẽ bằng PIL, webp 96px ≤24 khung, cache đĩa thumbs/shaders theo hash id+params); api/library.py: GET /api/shaders/{id}/preview?color=&speed=&still= (token header hoặc ?t=, header X-Shader-Preview animated|static; router chuyển sang dependency theo route); app ShaderPicker.vue (thumbnail 56px, tĩnh khi nghỉ, động khi hover/focus, reduced-motion giữ tĩnh, lỗi ảnh thì ẩn), composables/shaderPicker.ts (shaderPreviewUrl dùng window.studio.getConnection nên không cần hot file); tests/test_shader_preview.py.
Tiêu chí: đạt. Không có CPU port sẵn -> tự viết; speed=0 không có demo -> ảnh tĩnh + header static.
Test: pytest shader_preview+library_import+shaders 23 pass; cold (tốt nhất 3 lần): hit_flash 11, dissolve 46, outline_pulse 73, wind_sway 54, water_ripple 55, palette_swap 32 ms (<200). Typecheck sạch.
Nhìn: docs/qa/r16/WP-16B/01_pack_dialog_hover.jpg (vite mock + core thật, bridge giả), 02_all_presets_frames.png.
Hot file cần thêm: không bắt buộc. Tuỳ chọn: client.ts StudioApi.shaderPreviewUrl, mock trả ''. Lưu ý mock catalog dùng id khác core nên chip mock không có ảnh (ẩn gọn).
Commit message đề xuất: feat(shaders): animated CPU previews for shader presets (GET /api/shaders/{id}/preview) and thumbnails in the shader picker
