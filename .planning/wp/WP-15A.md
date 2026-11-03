## WP-15A: MCP cancel_scan + nợ nhỏ huỷ/seed
- Mục tiêu:
  1. MCP tool `cancel_scan(kind?: str, job?: str)` trong core/studio_core/mcp/server.py gọi POST /api/scans/cancel (xem cách các tool khác gọi API trong file), trả số job đã huỷ. Test trong core/tests/test_agent_mcp*.py (hoặc file test MCP hiện có). Chạy `cd core && .venv/Scripts/python.exe ../scripts/gen_agent_docs.py` để sinh lại docs.
  2. Project QA: bước Claude vision kiểm `job.cancelled` giữa các ảnh/lô (không giết request đang bay) → huỷ được trong ≤ 1 ảnh. Tìm chỗ gọi vision trong project_qa (grep project_qa trong core/studio_core).
  3. Rerun `seed: same`: step `map` (recipe có step map/fan-out) phải pin seed như step thường — xem engine/seeds.py + rerun_run trong runner.py; viết test fail trước, pass sau.
- Phạm vi: core/studio_core/mcp/server.py, engine/seeds.py, engine/runner.py (chỉ phần seed/map), file project_qa, test mới, docs sinh lại. Cấm: app/, api/types.ts, client.ts.
- Model: sonnet. Không GPU.
- Nghiệm thu: test mới pass; `cd core && .venv/Scripts/python.exe -m pytest tests -q -p xdist -n auto --dist loadfile` không giảm (1785 pass); test_agent_mcp không drift.
- Nhìn thấy được: không.
- Báo cáo: `.planning/wp/WP-15A-report.md`
## Nhật ký
- audit diff: scan_jobs/qa.py cancel route đã có; chỉ thiếu MCP tool, vision chạy ngoài job, map step không pin seed
- mcp/server.py: thêm cancel_scan(kind, job); test_mcp_r12::test_cancel_scan_tool pass; gen_agent_docs không đổi gì
- api/project_qa.py: _vision_pass chạy trong job, tick giữa từng ảnh + kiểm job.cancelled sau review; test_scan_progress pass
- seeds.py/runner.py: test_rerun_same_pins_each_item_seed_of_mapped_step fail (SeedUnknownError) -> pass sau khi pin _item_seeds theo item
- full pytest: 1788 passed, 6 skipped
