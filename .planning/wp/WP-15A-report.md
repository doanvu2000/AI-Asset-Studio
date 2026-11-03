## WP-15A — DONE
- Đã đổi: core/studio_core/mcp/server.py (tool cancel_scan) · api/project_qa.py (_vision_pass trong job, tick giữa ảnh, kiểm cancelled sau review) · engine/seeds.py (ITEM_SEEDS, _item_pins, pin_overrides cho step map) · engine/runner.py (_run_executor áp pin theo item) · tests: test_mcp_r12.py, test_scan_progress.py, test_rerun_seed.py
- Tiêu chí: 3/3
- Test: pytest tests -q -p xdist -n auto --dist loadfile -> 1788 passed, 6 skipped (1785 + 3 mới). Test seed map fail trước (SeedUnknownError), pass sau. gen_agent_docs không đổi file nào.
- Hot file cần thêm: không
- Commit: feat(core): cancel_scan MCP tool, cancellable project QA vision pass, pin per-item seeds on mapped step rerun
