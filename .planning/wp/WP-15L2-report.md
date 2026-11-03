## WP-15L2 — PARTIAL
- Đã đổi: composables/shaderPicker.ts (mới: chọn/tweak/options, chung key localStorage export:shaders), components/ShaderPicker.vue (mới: chip + màu/tốc độ), BundleMenu.vue dùng lại (xoá code trùng), PackDialog.vue: mục Shader khi chọn đích flutter/android/ios/unity/godot, danh sách lấy từ window.studio.asset.bundleOptions của tối đa 6 asset (chỉ bước sprite có catalog), gửi `shaders`/`shader_params` trong buildPack; hint "Android 13+ (AGSL) / iOS 17+ (Metal) nhận cả bộ shader".
- Tiêu chí: 1 đạt (code), 2 không cần sửa mock, 3 chưa làm: client không có API import (chỉ MCP) -> dừng, đề xuất bên dưới.
- Test: typecheck sạch. Body gửi đi chưa kiểm bằng core thật (mock không ghi body) — mới review code; pack android kotlin/AssetStudioShaders.kt chưa chạy thật.
- Nhìn: docs/qa/r15/WP-15L2/01_pack_shader_picker.jpg (vite mock, stub window.studio; mock không có window.studio nên ngoài Electron mục Shader ẩn).
- Hot file cần thêm: api/client.ts `importToLibrary(paths, projectId?)` + core endpoint import (mục 3); mock/server.ts không cần đổi. Có thể thêm endpoint core `GET shaders` để PackDialog khỏi dò từng step.
- Commit: feat(ui): shader picker in Pack dialog shared with export menu, sending shaders and shader_params for Android/iOS/Unity/Godot packs
