## WP-18B: Nhập thư viện từ blob + phím V + recipe CPU có gate.pick
- 1. Core POST /api/library/import (core/studio_core/api/library.py) nhận `hashes: string[]` (blob đã trong kho, kiểm regex hash + tồn tại) thay/hoặc kèm `paths`; MCP import_to_library thêm tham số `hashes` (mcp/server.py). Test.
- 2. App: client importToLibrary body thêm hashes; sendTo.ts đích 'import' nhận blob hash (không cần importPaths) → hiện ở cổng duyệt / Lightbox / RunDetail; LibraryImportDialog hiện tên ứng viên.
- 3. CommandPalette bảng phím: dòng "V — xem lớn (cổng chọn ảnh)".
- 4. Recipe CPU mẫu có gate.pick (vd `ui-icon-variants` hoặc tên hợp lý: tạo N biến thể màu/hue từ 1 ảnh bằng PIL → gate.pick → xuất) theo mẫu recipe CPU có sẵn (core/studio_core/recipes/*.yaml, ui-juice); chạy `cd core && .venv/Scripts/python.exe ../scripts/gen_agent_docs.py`; test recipe chạy không GPU.
- Phạm vi: core api/library.py, mcp/server.py, recipes/ + op nếu cần, tests; app api/types.ts, client.ts, mock/server.ts (gói này là chủ hot file), composables/sendTo.ts, components/LibraryImportDialog.vue, SendToMenu.vue, CommandPalette.vue (1 dòng). Cấm: GalleryView/Lightbox (18C), audit_flutter (18A).
- Model: sonnet. Không build. pytest song song + typecheck. Ảnh docs/qa/r18/WP-18B/.
- Báo cáo: `.planning/wp/WP-18B-report.md`
## Nhật ký
- core: library hashes + MCP hashes + op.hue_variants + recipe ui-icon-variants + gen_agent_docs; pytest viết xong
- app: types/sendTo/gateUtil/SendToMenu/LibraryImportDialog/mock/CommandPalette V sửa xong; typecheck sạch
- NHÌN: mock cổng 5318 (đã tắt theo PID cổng), docs/qa/r18/WP-18B/01,02; pytest toàn core xanh; typecheck sạch
