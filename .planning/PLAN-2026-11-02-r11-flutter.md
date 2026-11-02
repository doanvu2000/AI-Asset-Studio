# PLAN 2026-11-02 — xuất Flutter / Android native (lượt 11)

Chủ nhân: 99% dự án là Flutter (chính, cả game Flame) + Android native; Unity/Godot chưa dùng → Flutter thành đích mặc định.
Khảo sát: exporter đọc DTO `Source` (export/source.py) trả `Files`; đăng ký ở store/bundle.py:25 ENGINES, store/pack.py:32 PACK_ENGINES, api/pack.py:31, engine/exports.py:6, contracts.py:160 engine_target. Chưa có gì cho Flutter/Android.

| WP | Việc | File | Phụ thuộc |
|---|---|---|---|
| FL-1 | export/flutter.py + export/flutter_dart.py: ảnh UI/icon 1x/2.0x/3.0x (WebP tuỳ chọn), sprite sheet Flame (atlas JSON TexturePacker + anim JSON + Dart helper SpriteAnimation), parallax JSON + Dart ParallaxComponent, bitmap font, Spine copy, 9-slice JSON + Dart centerSlice/NineTileBox, sinh pubspec snippet + Assets.dart hằng số | file mới + test | không |
| AN-1 | export/android.py: drawable-mdpi..xxxhdpi WebP, .9.png, animation-list XML, mipmap + adaptive icon (foreground/background/monochrome + anydpi-v26) | file mới + test | không |
| REG-1 | Đăng ký target flutter/android: bundle.py, pack.py (roots + finalize: pubspec/Assets.dart cho cả gói), api/pack.py (engines list, bỏ max 2), engine/exports.py, contracts.py engine_target (+flutter, android; mặc định flutter cho dự án mới), api/projects.py, mobile.apply no-op | sau FL-1/AN-1 khung + sau W1-B/C commit |
| APP-1 | App: types EngineTarget/PackEngine, PackDialog chọn nhiều đích, ProjectDetail radio (Flutter đầu tiên), ExportToEngine nhãn "Xuất cho Flutter/Android", BundleMenu, GettingStarted, mock | sau REG-1 |
| FL-2 (sau) | App icon cấu hình flutter_launcher_icons.yaml, tileset .tmx cho flame_tiled, VFX Flame particles, SVG/VectorDrawable | đợt sau |

## Bổ sung (chủ nhân 2026-11-02): iOS SwiftUI + Android = Jetpack Compose
| WP | Việc | Phụ thuộc |
|---|---|---|
| AN-1 (cập nhật) | thêm Kotlin AssetStudioAssets.kt: composable chạy frame, NinePatch cho Compose, hằng drawable | — |
| IOS-1 | export/ios.py: Assets.xcassets (imageset @1x/@2x/@3x + Contents.json, slicing capInsets cho 9-slice, AppIcon.appiconset 1024 single-size, colorset), sprite anim = .spriteatlas hoặc frame imageset + Swift `AssetStudioAnimation` (TimelineView), font + Info.plist UIAppFonts snippet, Swift hằng tên asset, README_IOS.md | file mới, giao khi có slot |
REG-1 / APP-1 thêm target `ios`.
