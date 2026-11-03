# R12-RES — 6 ứng viên tính năng mới (lượt 12), soạn 2026-11-03

Phát hiện nền: NF-A/B/C xong cho FLUTTER (flutter_fx*.py, flutter_shaders.py, flutter_demo*.py). Nhưng **Android Compose / iOS SwiftUI chưa có**
tile_anim, autotile, weather, vfx_particles, card_set, minimap, ui_screen, shader/đèn (grep android.py/ios.py: chỉ có sheet, 9-slice, parallax, font).
Chưa trùng r5–r11 ideas (đã grep). Effort: gói = 1 subagent work package.

## Xếp hạng
| Hạng | ID | Tính năng | CPU/GPU | Gói | Rủi ro |
|---|---|---|---|---|---|
| 1 | G | Parity Compose + SwiftUI cho các kind NF-B | CPU | 2 | thấp |
| 2 | D | Depth/normal AI (MoGe-2-small) cho đèn 2D | CPU/GPU nhẹ | 1–2 | thấp–vừa |
| 3 | F | Theme UI từ style bible → Flutter ThemeData / Compose / SwiftUI | CPU | 1–2 | thấp |
| 4 | H | Trim/decal + shader pack cho Compose (AGSL) / SwiftUI (Metal) | CPU | 2 | vừa (không test thiết bị) |
| 5 | E | Video → sprite (SAM3 video) | GPU nặng | 2 | cao |
| 6 | I | "Flutter project lint": quét project Flutter có sẵn, đề xuất/áp 1-chạm (WebP, 1x/2x/3x, pubspec) | CPU | 1–2 | vừa |

## G. Parity Compose + SwiftUI (đề xuất #1)
- Giá trị: chủ nhân nói Android = Compose, iOS = SwiftUI (PLAN-11-02-r11-flutter); hiện chỉ Flutter dùng được 7 kind mới; người làm game native bị hụt.
- Tái dùng: mẫu `export/flutter_fx.py` + `flutter_fx_dart.py` (logic kind → file), `export/android.py`, `export/ios.py`/`ios_swift.py` (khung Files + Kotlin/Swift text), `export/source.py` (DTO Source), danh sách kind flutter.py:61-66.
- Model: không. CPU. Đèn xanh mọi máy.
- Việc: G-1 Android (tile_anim → Compose Canvas frame loop, autotile → JSON + Kotlin tile renderer, weather → looping Canvas, ui_screen → Composable, card_set, minimap, vfx → particle Canvas). G-2 iOS tương tự (SwiftUI TimelineView/Canvas).
- Rủi ro: không có thiết bị Android/iOS để chạy; Kotlin compile cần Gradle (chủ nhân cho build trong lượt này); Swift chỉ kiểm cú pháp tĩnh (không có Xcode).
- Nghiệm thu: pytest mỗi kind × 2 đích (tên file, JSON schema, tham chiếu asset tồn tại); Android: tạo project Compose mẫu trong scratchpad, `gradlew compileDebugKotlin` 0 lỗi cho đủ 7 kind; iOS: `swiftc -parse` nếu có, nếu không kiểm cú pháp bằng test chuỗi + nêu rõ "chưa biên dịch"; pytest toàn bộ không giảm (1625 pass).

## D. Depth/normal AI cho đèn 2D
- Giá trị: sprite-normal-map hiện chỉ filter CPU (ops/normal2d.py, 81 dòng; export/normal2d.py) → đèn phẳng. Normal từ depth AI cho Flame lit_sprite (NF-A) nhìn có khối thật; hợp game top-down/2.5D.
- Model: MoGe-2 ViT-S normal (Ruicheng/moge-2-vits-normal, HF tag MIT, ~35M params, chạy CPU vài giây/ảnh) — kiểm file LICENSE repo microsoft/MoGe + HF khi tải; dự phòng Depth-Anything-V2-Small (Apache-2.0, HF tag; Base/Large/Giant là CC-BY-NC → KHÔNG dùng). Torch CPU, venv riêng, không động core venv.
- Tái dùng: ops/normal2d.py (thêm backend "ai" + fallback filter), recipes/sprite-normal-map.yaml, export/flutter_shaders.py (lit_sprite đã đọc normal), bootstrap/manifest.json (pack nhỏ + hw), hardware.py fit.
- Rủi ro: ảnh sprite pixel/nền trong suốt là ngoài phân phối; cần mask alpha, ép depth về dải nhỏ; MoGe-2 cần cài dependency (utils3d) trên Windows.
- Gói: 1 (core + recipe option `method: ai|filter`), +1 nếu thêm UI nút.
- Nghiệm thu: 10 sprite mẫu (8dir, prop, icon): normal hợp lệ (|n|=1 ±0.02, alpha giữ), thời gian ≤ 5 s/ảnh CPU, golden lit_sprite so với filter cho thấy gradient mượt hơn (đo: độ lệch góc normal trung bình so với bản Blender-bake của prop 3D ≤ filter hiện tại); fallback filter khi thiếu model; fit = xanh trên máy chỉ CPU.

## F. Theme UI từ style bible
- Giá trị: dev mobile cần `ThemeData`/`ColorScheme`, `MaterialTheme` (Compose), SwiftUI `Color`/`Font` token nhất quán với art; hiện ui-kit chỉ ra ảnh + 9-slice.
- Tái dùng: store style bible (build_style_bible, get_style_bible), ops/ui.py, ops/lut.py (trích palette), bitmap_font, export/flutter.py (assets), export/android.py (res/values), export/ios.py (colorset), ui_screen exporter.
- Model: không (palette trích bằng k-means CPU + luật tương phản WCAG; Claude chỉ gợi tên token, tuỳ chọn).
- Rủi ro: thấp; "đẹp" khó đo → đo bằng tương phản.
- Gói: 1–2. Nghiệm thu: sinh `app_theme.dart` (dart analyze 0, flutter test dựng MaterialApp), `Theme.kt`, `AppTheme.swift`; mọi cặp text/nền đạt WCAG AA ≥ 4.5; light + dark; pytest.

## H. Trim/decal + shader cho Compose/SwiftUI
- Trim/decal đã bị bỏ ở NF-B (PLAN new3). Shader Pack Flutter có 6 hiệu ứng; AGSL (Android 13+) và Metal SwiftUI `.colorEffect` port được từ export/shader_templates/ + flutter_shaders.py. Rủi ro: AGSL chỉ API 33+, cần fallback; không test máy thật. Hạng thấp vì chủ nhân làm Flutter chính.
- Nghiệm thu: file .agsl/.metal sinh đúng uniform như bản .frag, test cú pháp, golden CPU có sẵn.

## E. Video → sprite (SAM3 video)
- Đã có sprite-anim-video (Wan 2.2 TI2V + BiRefNet, ops/anim_video.py 39 KB) cho 1 ảnh → anim. Ứng viên E là video NGƯỜI DÙNG → sprite (SAM3 tracking). SAM3 license: SAM License (tự kiểm LICENSE, gated HF cần đăng nhập). GPU nặng, chia GPU với app khác → loại khỏi top 3. Phần rẻ: nhập mp4 vào anim_video.py, bỏ qua Wan, dùng BiRefNet (đã có) — có thể là option của G sau này.

## I. Flutter project lint + sửa 1 chạm
- Tái dùng export/audit.py (49 KB), audit_media.py, mobile.py (ngân sách), restyle. Giá trị: quét `pubspec.yaml`/assets Flutter có sẵn: ảnh quá to, thiếu 2x/3x, PNG → WebP, asset không dùng, khai báo pubspec thiếu. Nghiệm thu: project Flutter mẫu có 10 lỗi cài sẵn → bắt đủ 10, sửa tự động giảm ≥ 40% MB, `flutter test` còn pass. Gói 1–2. Hạng 6 vì trùng 1 phần audit/mobile (#31, #52).

## Top 3 đề xuất lượt 12
1. G (parity Compose + SwiftUI): khoảng trống rõ nhất sau NF-B, CPU, đúng ưu tiên Android/iOS của chủ nhân. Làm G-1 Android trước.
2. D (normal AI MoGe-2-S): nâng chất lượng đèn 2D vừa làm ở NF-A; model nhỏ chạy CPU, license MIT (kiểm file thật).
3. F (theme từ style bible): CPU, S–M, dùng được cả 3 đích, hợp Flutter mặc định.
Thứ tự gợi ý: F (nhanh, độc lập) → G → D (D cần cài model, chạy nền song song được vì ít chạm export). G và F cùng chạm android.py/ios.py/flutter.py → tuần tự.
Licenses đã xem qua HF metadata (chưa đọc file LICENSE gốc): MoGe-2-vits MIT, Depth-Anything-V2-Small Apache-2.0. Phải đọc file LICENSE repo trước khi vào manifest.
