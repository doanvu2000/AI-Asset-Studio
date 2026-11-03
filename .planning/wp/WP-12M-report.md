## WP-12M — DONE
Đã đổi
- core/studio_core/mcp/server.py: update_project +auto_export/auto_export_dir (kết quả trả thêm 2 field); tool mới rerun_run(run_id, seed new|same); build_asset_pack +theme; tool mới get_ui_theme(project_id, target, package).
- integrations/claude-plugin/skills/asset-studio/SKILL.md (+ codex sync qua gen_agent_docs): nhắc 4 tính năng.
- core/tests/test_mcp_r12.py (mới, 3 test).
Tiêu chí 2/2: test MCP cho 4 mục; test_agent_mcp + test_p4b_mcp pass, docs --check rc=0.
Audit: diff chỉ trong mcp/, integrations/, tests; không đụng engine/store/api/app.
Review: lỗi seed_unknown/conflict từ core đi qua _call thành "seed_unknown: ..." (ToolError). target/package sai bị core từ chối (409/422 -> is_error). auto_export_dir "" vẫn gửi (không None) để xoá.
Test: `pytest tests/test_agent_mcp.py tests/test_p4b_mcp.py tests/test_mcp_r12.py -n auto --dist loadfile` → 18 passed; `gen_agent_docs.py --check` → rc 0.
CHƯA KIỂM: rerun seed="same" thành công (recipe test CPU không ghi seed nên chỉ kiểm nhánh seed_unknown nếu có lỗi); ruff không cài trong venv.
Ngoài phạm vi: rerun không kế thừa gate_policy của run gốc (hành vi core, run mới dừng ở awaiting_human).
Commit message đề xuất: feat(mcp): add rerun_run and get_ui_theme tools, theme option for build_asset_pack, auto_export fields on update_project
