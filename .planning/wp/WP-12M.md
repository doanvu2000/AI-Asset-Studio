## WP-12M: MCP cho tính năng lượt 12
- Mục tiêu (MCP server core/studio_core/mcp/server.py + module tools nó gọi):
  1. `update_project`: thêm `auto_export: bool | None`, `auto_export_dir: str | None` (đi qua PATCH project đã có, commit ea5b588).
  2. Tool mới `rerun_run(run_id, seed: 'new'|'same' = 'new') -> {run_id}` gọi logic rerun (engine/runner.rerun_run, commit 56a6ca2); lỗi seed_unknown trả thông điệp rõ.
  3. `build_asset_pack` (hoặc tool pack tương ứng): thêm `theme: bool = False` (PackBody.theme, commit c10ed32).
  4. Tool đọc theme: `get_ui_theme(project_id, target='flutter'|'android'|'ios', package=None)` gọi GET /api/projects/{id}/style/theme — trả files.
  5. Cập nhật mô tả MCP server instructions nếu có danh sách tool; chạy `cd core && .venv/Scripts/python.exe ../scripts/gen_agent_docs.py` để docs/skill đồng bộ; test_agent_mcp pass.
- Phạm vi: core/studio_core/mcp/**, integrations/** (chỉ file sinh tự động), core/tests/test_p4b_mcp.py hoặc test mcp mới. | Cấm: app/**, core/studio_core/engine/** (chỉ gọi), store/**, api/** (chỉ gọi). Agent khác đang sửa app/** (WP-12FX, WP-12N).
- Model: sonnet.
- Tiêu chí:
  - [ ] Test MCP cho 4 mục (gọi tool qua cách test MCP hiện có làm).
  - [ ] test_agent_mcp + test_p4b_mcp pass; docs sinh lại không drift.
- Báo cáo: `.planning/wp/WP-12M-report.md`
## Nhật ký
- Sửa server.py (update_project auto_export*, rerun_run, build_asset_pack theme, get_ui_theme), SKILL.md + sync codex, test_mcp_r12.py; pytest 18 pass, docs check rc=0.
