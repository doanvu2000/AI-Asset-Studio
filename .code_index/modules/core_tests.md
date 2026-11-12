# core_tests — pytest, script kiểm

Gốc `core/tests/` (~160 file), `core/scripts/`, `scripts/`. Không có test frontend.

## Chạy
- Chuẩn: `node scripts/check.mjs` (kiểm dòng > 120 ký tự trong `core/*.py` đổi → pytest `-m "not slow and not live"`, `-n` theo luồng, `--tb=line`). Script: `scripts/check.mjs`, thư viện bước `scripts/lib/jin-steps.mjs`.
- Cờ: `--tests tests/test_a.py,tests/test_b.py` · `--slow` · `--only pytest` · `--skip lines` · `--max-len 120` · `--verbose`.
- Trực tiếp: `core/.venv/Scripts/python.exe -m pytest core/tests/<file>` (từ `core/`, `testpaths=["tests"]`, `asyncio_mode="auto"`, có `pytest-xdist`).
- Marker (`core/pyproject.toml`): `slow` (> ~5s), `live` (cần GPU/ComfyUI/Blender/Codex thật, chạy bằng `-m live`).

## Fixture + fake
- `core/tests/conftest.py` — `pytest_configure` + `pytest_collection_modifyitems` (xử lý marker), fixture `app_ctx` (app + `httpx.AsyncClient` + `StubEngine`); đọc trước khi viết test mới.
- `core/tests/test_adapters_fakes.py` — executor/resource giả (không cần GPU) dùng làm mẫu cho test engine.
- Test API: `build_app(settings, token, engine=...)` + `fastapi.testclient.TestClient` / `httpx.AsyncClient`; xem `core/tests/test_api_basic.py`.

## Nhóm test theo vùng
- Engine: `test_engine_runner.py`, `test_engine_recipe.py`, `test_p2_scheduler.py`, `test_gpu_yield.py`, `test_chain.py`, `test_w9_flows.py`, `test_flow_batch.py`, `test_rerun_seed.py`, `test_auto_pick_seeds.py`, `test_perf_lanes_cache.py`, `test_w9_cache_scope.py`.
- API/store: `test_api_basic.py`, `test_p6_recipes_api.py`, `test_w8_projects.py`, `test_store_repo.py`, `test_store_blobs.py`, `test_store_events.py`, `test_versions_paging.py`.
- Export: `test_export_*.py` (flutter, android, ios, engines, shaders), `test_step_export.py`, `test_pack.py`, `test_theme_export.py`, `test_audit*.py`, `test_shaders.py`.
- Ops/recipe: `test_ops_executors.py`, `test_ops_image.py`, `test_p2_recipes.py`, `test_p7_kinds.py`, `test_<op>.py` (autotile, pbr, pixel_cleanup, npc, card_set, ...), `test_r*_*.py` / `test_w*_*.py` theo đợt tính năng.
- Adapter: `test_adapters_comfy.py`, `test_adapters_cloud.py`, `test_p4_blender.py`, `test_rig3d.py`, `test_setup.py`, `test_hardware_fit.py`; `test_adapters_live.py` = live.
- MCP: `test_agent_mcp.py`, `test_mcp_r12.py`, `test_p4b_mcp.py`, `test_p4b_lock.py`.

## Script tay (`core/scripts/`)
- `e2e_r1.py`, `e2e_r5.py`, `e2e_r5mv.py`, `e2e_r6.py`, `e2e_r7.py`, `e2e_skin_variant.py`, `e2e_text.py` — chạy recipe thật qua core/ComfyUI; `verify_shaders.py` — dịch/kiểm shader template. Tốn GPU, không nằm trong `check`.
- `scripts/gen_agent_docs.py` — sinh `references/recipes.md` cho plugin Claude/Codex từ recipe YAML.
- `app/scripts/art-manifest.json` + `app/scripts/gen-art.py` — sinh ảnh minh hoạ recipe cho app.

## Quy ước thấy trong code
- Test không gọi mạng/GPU trừ marker `live`; backend ngoài thay bằng fake.
- Test đặt tên theo đợt (`test_p*` pha, `test_r*` round, `test_w*` wave) hoặc theo module (`test_<module>.py`); thêm test mới theo module.
