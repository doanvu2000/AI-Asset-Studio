## WP-19A: Nhập thư viện mỗi file 1 asset
- Hiện: /api/library/import (core/studio_core/api/library.py:111-117) nhiều path/hash = 1 run → 1 asset nhiều version (QA r18 quan sát).
- Mục tiêu: body thêm `group: "per_file" | "single"` (mặc định "per_file" khi >1 file, giữ "single" khi 1 file hoặc asset_name được đặt); per_file = mỗi file 1 run/asset tên theo stem (trùng tên thì thêm số), trả danh sách {run_id, versions}. Giữ tương thích response cũ (thêm field `items`, giữ `run_id`/`versions` của item đầu nếu cần — đọc client hiện dùng gì). MCP import_to_library thêm `group`. App LibraryImportDialog: radio "Mỗi tệp một asset" / "Gộp thành 1 asset (nhiều phiên bản)" + ô tên khi gộp; toast nêu số asset.
- Phạm vi: core api/library.py, mcp/server.py, tests (test_library_import*.py); app api/types.ts, client.ts, mock/server.ts, components/LibraryImportDialog.vue. Cấm CommandPalette/sendTo.ts (19B), RunsView/RunDetail (19C).
- Model: sonnet. Không build. pytest song song + typecheck + gen_agent_docs. Ảnh docs/qa/r19/WP-19A/.
- Báo cáo: `.planning/wp/WP-19A-report.md`
## Nhật ký
- 19A core: library.py per_file/single + items, mcp group, tests (35 pass)
- 19A app: types/client(no change needed)/mock/dialog; typecheck running
- 19A DONE, report written
