## WP-15C: Endpoint catalog shader + nhập thư viện từ Audit/Restyle
- Mục tiêu:
  1. Core `GET /api/shaders` trả `export/shaders.catalog()` (cùng shape ShaderPresetInfo mà preload bundleOptions trả). Client `shaderCatalog()`; composables/shaderPicker.ts / PackDialog dùng endpoint này thay vì dò bundleOptions từng asset (giữ BundleMenu như cũ nếu nó lấy từ bundleOptions của chính step). Mock trả catalog mẫu.
  2. Core `POST /api/library/import {paths: string[], project_id?: str, asset_name?: str, approve?: bool}` dùng chung logic với MCP import_to_library (core/studio_core/mcp/server.py ~620-645 `import_files`: chạy recipe import + approve). Đưa logic dùng chung vào core (không gọi HTTP chính mình nếu tránh được; nếu MCP chỉ là client HTTP thì endpoint mới thực hiện trực tiếp phía server, MCP tool đổi sang gọi endpoint mới). Kiểm path tuyệt đối, tồn tại, đuôi file hợp lệ; trả {run_id, versions:[...]}.
  3. Client `importToLibrary(body)`; SendToMenu đích "Nhập vào thư viện" cho Audit (file chọn / không ai dùng) và Restyle kết quả (khi kết quả là file ngoài thư viện); chọn project (mặc định project hiện tại), toast có link mở Gallery lọc version mới.
- Phạm vi: core api (file route mới hoặc routes.py), mcp/server.py (đổi import_to_library), test core/tests/test_library_import.py, test_shader_catalog; app api/types.ts, api/client.ts, api/mock/server.ts (gói này là chủ hot file), composables/shaderPicker.ts, PackDialog.vue, SendToMenu.vue, sendTo.ts, AuditView/RestyleView (chỉ phần gắn đích).
- Model: sonnet. Không build. pytest (-p xdist -n auto --dist loadfile), `cd app && npm run typecheck`, gen_agent_docs nếu đổi MCP (`cd core && .venv/Scripts/python.exe ../scripts/gen_agent_docs.py`).
- Nghiệm thu: test endpoint import (file thật tmp, project, approve) + catalog; MCP import_to_library test cũ pass; Audit → Nhập thư viện tạo version thấy trong Gallery (Electron thật + core thật, data tạm); PackDialog hiện shader không cần asset sprite có step; full pytest không giảm (1821); typecheck sạch.
- Nhìn thấy được: có — ảnh docs/qa/r15/WP-15C/.
- Báo cáo: `.planning/wp/WP-15C-report.md`
## Nhật ký
- 1) core: api/library.py (GET /api/shaders, POST /api/library/import), MCP import_files gọi endpoint mới
- 2) test_library_import.py (7 test) + MCP tests chạy
- 3) app: types/client/mock (shaderCatalog, importToLibrary)
- 4) UI: LibraryImportDialog, SendToMenu/sendTo 'import', toast action, Audit/Restyle importPaths, PackDialog dùng shaderCatalog
- 5) pytest 1828 pass, typecheck sạch, nhìn xong (docs/qa/r15/WP-15C), report ghi
