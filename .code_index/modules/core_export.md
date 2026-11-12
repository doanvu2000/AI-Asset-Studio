# core_export — export engine, atlas, shader, theme, audit

Gốc `core/studio_core/export/` (thuần hàm, không IO mạng). Điểm vào từ `store/bundle.py` (xem `core_store.md`) và `api/pack.py`.

## Mô hình chung
- `export/source.py::Source` — gói dữ liệu của 1 step để export: sheet/layout, ảnh, tile, UI, mesh, audio, VFX...; dựng bởi `store/bundle.py::engine_source`.
- `export/sheet.py::Layout` · `Track` · `Cell` — lưới sprite sheet + clip; `compass` / `direction_vector` / `ident` / `cs_namespace` đặt tên hướng, định danh an toàn.
- Mỗi engine có `export(src: Source, ...) -> Files` (dict đường dẫn → bytes) — `Files` ghi ra đĩa bởi `store/bundle.py::_write`.
- `store/bundle.py::ENGINES` = `flutter` · `android` · `ios` · `unity` · `godot`; `MOBILE_ENGINES` = flutter/android/ios (không áp shader/toon/hit FX/texture budget của Unity/Godot).

## Từng engine
- Flutter/Flame (đích mặc định): `export/flutter.py::export` (+ `density_images`, `tile_files`, `icon_files`), `flutter_dart.py`, `flutter_demo.py` / `flutter_demo_dart.py` (demo chạy `flutter run`), `flutter_fx.py` / `flutter_fx_dart.py`, `flutter_shaders.py` (`.frag`).
- Android Compose: `export/android.py::export` (`nine_patch`, `sheet_files`, `app_icon_files`, `kotlin_source`), `android_fx.py` / `android_fx_kotlin.py`, shader AGSL `shaders_agsl.py`.
- iOS SwiftUI: `export/ios.py::export` (`imageset`, `app_icon_files`), `ios_swift.py`, `ios_fx.py` / `ios_fx_swift.py`, shader Metal `shaders_metal.py`.
- Unity: `export/unity.py::export` (`guid`, `texture_meta`, `anim_clip`, `controller`, `prefab`, `copy_unity_export`); GUID ổn định theo `(uid, asset, rel)`.
- Godot 4: `export/godot.py::export` (`sprite_frames`, `scene`, `tileset`, `wang_tileset`, `ui_scene`); `godot_copy.py`.
- Khác: `export/aseprite.py`, `spine_skeleton2d.py` / `spine_unity2d.py`, `destructible.py`, `lod.py`, `modular.py`, `rig3d.py`, `skybox.py`, `terrain.py`, `weather.py`, `vfx_particles.py`, `playable_scene.py`.

## Tính năng chung
- `export/atlas.py::Atlas` · `AtlasOptions` · `Sprite` · `Frame` — pack nhiều sprite vào atlas (API `atlas.py`, `build_atlas` trong audit).
- `export/collider.py::ColliderOptions` · `SheetCollider` · `alpha_mask` — collider polygon từ alpha; `default_enabled(recipe_id)`.
- `export/shaders.py::Preset` · `Param` · `ShaderChoice` · `ShaderError` — preset shader (dissolve, hit flash, outline pulse, palette swap, water ripple, wind sway, toon3d) + template trong `shader_templates/` (`.shader`, `.gdshader`, `flutter/*.frag`, `agsl/`, `metal/`).
- `export/normal2d.py`, `export/toon3d.py`, `export/pbr.py::urp_lit_material`, `export/hitfx.py::HitOptions` · `Hit` · `Vfx` (`is_attack(clip)`), `export/frame_data.py`, `export/damage.py`.
- `export/theme.py::build_schemes` · `contrast_report` · `ThemeSpec` — Material 3 scheme sáng/tối từ style bible, ép tương phản WCAG AA (`contrast`, `luminance`); đầu ra `theme_dart.py` / `theme_kotlin.py` / `theme_swift.py` qua `theme_files`.
- `export/mobile.py::Budget` — ngân sách texture/mesh mobile.
- `export/preview.py::preview_godot` · `preview_unity` · `PreviewJob` — chạy engine headless kiểm lỗi export (`godot_issues`, `unity_log_issues`, `availability`).

## Audit folder game (`export/audit*.py`)
- `export/audit.py::scan` (đọc project Unity/Godot/Flutter: texture, trùng, không dùng, âm thanh, tri) → `make_plan` / `plan_fixes` → `apply_fixes` (backup) → `undo` / `list_backups`; `patch_importer`, `safe_importer` (chặn path ngoài project), `build_atlas`.
- `export/audit_flutter.py::flutter_root` · `is_variant` (ảnh theo density `2.0x`/`3.0x`); `audit_media.py`; `audit_plans.py::PlanCache` (cache plan theo `actions_key`).

## Invariant
- Export chỉ ghi dưới thư mục đích (`store/export.py::export_copy` lọc tên); GUID Unity cố định theo nội dung để re-export không vỡ reference.
- Audit luôn backup trước khi sửa (`backup_dir`) và `undo` được; ghi file kiểu atomic (`_atomic_write`).
- Test: `core/tests/test_export_flutter.py`, `test_export_android.py`, `test_export_ios.py`, `test_export_engines.py`, `test_export_shaders_mobile.py`, `test_shaders.py`, `test_theme_export.py`, `test_audit.py`, `test_unity_shared_scripts.py`, `test_godot_shared_scripts.py`, `test_collider.py`, `test_hitfx.py`.
