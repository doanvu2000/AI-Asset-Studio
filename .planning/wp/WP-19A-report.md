## WP-19A — DONE
Đã đổi: core/studio_core/api/library.py (body `group`, `_import_run`, `_unique_names`; response thêm `group`+`items`, giữ run_id/versions của item đầu); mcp/server.py (import_to_library `group`, trả items); tests test_library_import.py, test_library_import_hashes.py, test_agent_mcp.py; app api/types.ts (ImportGroup, ImportedItem, result.items), mock/server.ts, components/LibraryImportDialog.vue (radio + ô tên + toast số asset). client.ts không cần đổi (body/result pass-through).
Quy tắc: default per_file khi >1 blob, single khi 1 file hoặc có asset_name; per_file + asset_name + nhiều file -> 400; trùng stem -> "x 2", "x 3"; path và hash cùng blob được gộp 1 unit.
Lỗi giữa chừng: asset đã tạo trước đó giữ nguyên, lỗi nêu "after N of M assets were already created: run ids" + detail.created_run_ids (test monkeypatch).
Test: pytest 4 file (library_import, hashes, agent_mcp, mcp_r12) 35 passed; npm run typecheck sạch; gen_agent_docs chạy không báo lỗi (không file docs đổi).
Nhìn: docs/qa/r19/WP-19A/01-03 (mock cổng 5191, đã tắt theo PID): mặc định "Mỗi tệp một asset", gộp hiện ô tên, toast "Đã nhập 3 asset" / "1 asset (3 phiên bản)".
Lưu ý: test cũ nhiều file = 1 run đã thêm group:"single". Chưa test ca trùng stem trong UI mock (đã test ở pytest).
Commit đề xuất: feat(library): import each file as its own asset by default (group per_file|single), items in response, MCP group option, dialog radio and asset count toast
