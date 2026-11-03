## WP-15S: Shader pack AGSL (Compose) + Metal (SwiftUI)
- Mục tiêu: xuất 6 hiệu ứng có bản Flutter .frag (core/studio_core/export/shader_templates/flutter/: dissolve, hit_flash, outline_pulse, palette_swap, water_ripple, wind_sway; lit_sprite là tuỳ chọn nếu port gọn) sang:
  - Android Compose: file .agsl (hoặc chuỗi trong Kotlin) + `Shaders.kt` dùng `RuntimeShader` + `Modifier.graphicsLayer { renderEffect = RenderEffect.createRuntimeShaderEffect(...) }`, gate `Build.VERSION.SDK_INT >= 33`, fallback là không hiệu ứng (hit_flash fallback ColorFilter). Uniform cùng tên/nghĩa như bản .frag.
  - iOS SwiftUI: `Shaders.metal` với `[[stitchable]]` hàm cho `.colorEffect`/`.distortionEffect`/`.layerEffect` (iOS 17+), `ShaderEffects.swift` có View modifier tiện dùng, `@available(iOS 17, *)`.
- Nối vào pack: chỗ Flutter shader được thêm vào pack (export/flutter_shaders.py, store/pack*.py) → thêm tương đương cho đích android/ios (xem cách android_fx*.py / ios_fx*.py nối vào pack). Mẫu sinh tên file nhất quán.
- Phạm vi: file mới export/shaders_agsl.py, export/shaders_metal.py (hoặc tương đương), shader_templates/agsl/, shader_templates/metal/, chỗ nối pack android/ios, test core/tests/test_export_shaders_mobile.py. Cấm: app/, mcp/server.py, audit*.
- Model: sonnet. Không build (không có Gradle project/Xcode): kiểm bằng test — uniform khớp bản .frag (parse), cú pháp cơ bản (cân ngoặc, mọi hàm `half4 main(float2` cho AGSL; `[[stitchable]]` + chữ ký đúng cho Metal), Kotlin/Swift tham chiếu đúng tên shader.
- Nghiệm thu: 6 hiệu ứng × 2 đích có file; test pass; pack android/ios có shader khi chọn; full pytest không giảm. Ghi rõ "chưa biên dịch thật" trong báo cáo.
- Nhìn thấy được: không.
- Báo cáo: `.planning/wp/WP-15S-report.md`
## Nhật ký
- wiring xong: shaders_agsl.py, shaders_metal.py, templates agsl/ metal/, android/ios export(shaders=), bundle.py+pack.py nối, spec datas
- test_export_shaders_mobile.py 19 pass; liên quan 352 pass; full 1817 pass, 1 fail test_engine_preview (pass khi chạy riêng, flaky song song)
