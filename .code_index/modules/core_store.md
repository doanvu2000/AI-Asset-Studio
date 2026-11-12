# core_store — SQLite, blob, event bus, pack

Gốc `core/studio_core/store/`. Triển khai `Repo`, `BlobStore`, `EventBus` của `contracts.py`.

## Lưu trữ
- `store/db.py::Database` — `aiosqlite`; `open` chạy `SCHEMA` (bảng runs, steps, versions, blobs, projects, cache, manifests, workflow_runs, workflow_batches, gdd_plans, run_presets, style_bibles), `write` / `write_many` / `fetchone` / `fetchall`.
- `store/repo.py::SqliteRepo` — CRUD: run (`create_run`, `list_runs`, `run_preview_hashes`), step (`create_steps`, `get_steps`, `list_steps_by_status`, `recent_failed_steps`), cache (`cache_get`, `cache_get_meta`, `cache_put`), version (`add_version`, `set_version_status`, `set_version_qa`, `list_versions`, `count_versions`, `versions_to_score`), blob row, project, run preset, manifest, style bible, workflow run/batch, `project_summary`.
- `store/repo.py::_Table` — map pydantic ↔ cột (encode JSON, decode).
- `store/blobs.py::FsBlobStore` — content-addressed: `put_file`, `put_bytes`, `get`, `path(hash)`, `thumbnail(hash, size)`; cùng nội dung = cùng hash.
- `store/events.py::MemoryEventBus` — `publish(type, data, run_id)` gán `seq` tăng dần, ring buffer (`deque`, replay); `subscribe(after_seq)` replay rồi stream; `last_seq`.
- `store/stats.py::Filters` — query thống kê recipe/step/ngày (cho `api/stats.py`).

## Export file / bundle / pack
- `store/export.py::export_copy` · `safe_filename` · `safe_component` — chép blob ra đĩa, tên hợp lệ Windows (bỏ ký tự cấm, tên reserved `CON`/`NUL`..., `MAX_NAME` 120).
- `store/bundle.py::export_engine` — step → thư mục engine (`ENGINES` map flutter/android/ios/unity/godot → `export()`), `engine_source` dựng `Source` từ outputs, `engine_ready`, `export_bundle` (thư mục thô hoặc repack sheet theo `ExportPreset`), `sheet_meta`, `BundleError`.
- `store/pack.py::PackOptions` · `EngineOptions` · `PackAsset` · `finalize_unity` · `finalize_godot` · `asset_provenance` — gói nhiều asset đã duyệt của project thành package/addon (Unity asmdef, Godot addon, guid scope); `next_version` semver, `load_history` / `save_history`.
- `store/pack_mobile.py` — pack Flutter / Android / iOS (`MOBILE_ENGINES`).
- `store/ai_credits.py::disclosure` · `license_warnings` — tổng hợp model đã dùng + giấy phép (`store/model_licenses.py::model_license`) cho file credits.

## Invariant
- Blob không sửa tại chỗ; xoá/ghi lại = hash mới. Cache chỉ tính hit khi blob còn tồn tại.
- Event `seq` đơn điệu; client reconnect dùng `after_seq` để không mất event.
- Test: `core/tests/test_store_repo.py`, `test_store_blobs.py`, `test_store_events.py`, `test_blob_export.py`, `test_pack.py`, `test_pack_mobile.py`, `test_versions_paging.py`.
