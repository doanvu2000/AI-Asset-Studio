## WP-16A: Android/iOS xuất ảnh tĩnh
- Mục tiêu: version ảnh đơn (PNG/JPG/WebP nhập qua library-import, icon, ảnh không phải sheet) xuất được cho android (res/drawable-mdpi..xxxhdpi hoặc nodpi theo kích thước, tên snake_case hợp lệ resource, Kotlin object tham chiếu R.drawable.*) và ios (Assets.xcassets/<name>.imageset với Contents.json 1x/2x/3x, Swift enum/extension tham chiếu Image("<name>")). Pack có cả ảnh tĩnh lẫn sheet. Tái dùng density logic sẵn có (export/android.py, ios.py, flutter.py density 1x/2x/3x).
- Hiện: store/pack.py skip với warning (af524d7, test core/tests/test_pack_mobile.py only-unexportable) — đổi test cho đúng hành vi mới, giữ skip cho loại thật sự không xuất được (glb…).
- Phạm vi: core export/android*.py, ios*.py, store/bundle.py, store/pack.py, test. Cấm app/, mcp/server.py, export/shaders*.
- Model: sonnet. Không build. pytest song song.
- Nghiệm thu: test ảnh 256/1024 px → đủ density, tên hợp lệ (bắt đầu chữ, chỉ [a-z0-9_]), Contents.json hợp lệ; pack project chỉ PNG nhập cho android+ios ra file; full pytest không giảm (1830).
- Nhìn thấy được: không.
- Báo cáo: `.planning/wp/WP-16A-report.md`
## Nhật ký
- AUDIT: pack skip nằm ở pack._export_one (engine_ready=False); exporter android/ios đã xử lý src.ui borderless -> tái dùng.
- CODE: bundle.py still_images/_source_ready/engine_ready(engine,stills)/export_engine(stills=); pack.py truyền version blob.
- TEST: test_pack_mobile đổi test Icon, thêm glb-only skip + density 256/1024 png/jpg/webp. Full: 1838 pass, 6 fail test_shader_preview (agent khác).
