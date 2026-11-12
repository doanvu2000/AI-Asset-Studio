# core_engine — run / step / gate / scheduler / flow

Gốc `core/studio_core/engine/`. Engine là lõi: nhận recipe + inputs, dựng step graph, chạy executor theo lane, lưu qua `Repo`.

## Chạy 1 run
- `engine/runner.py::StudioEngine` — facade `Engine`: `start_run`, `rerun_run`, `resolve_gate`, `retry_step`, `cancel_run`, `recover`, `wait_idle`, `check_inputs`, `list_recipes`.
- Luồng: `start_run` → dựng `StepRow` từ `Recipe` → `_start_driver` → `_drive` lặp `_pass` (spawn step có dependency xong) → `_exec_step` → `_finish_success` | `_abort`.
- `_exec_step`: resolve executor → `_resolve_inputs` / `_resolve_refs` → `executor_cache_key` → cache hit (`StepStatus.CACHED`, bỏ qua nếu `fresh` hoặc `NO_CACHE`) → `scheduler.acquire(lane)` → `_ensure_resource` → `_run_with_auto_retry` → ghi output + `repo.cache_put`.
- `engine/runner.py::_Ctx` — `StepContext` đưa cho executor: `progress`, `add_cost`, `log`, `preview`, `set_item` (map nhiều item).
- Lỗi: `NotFoundError`, `EngineStateError`, `SeedUnknownError`; lỗi step = `StepError(code, message, retryable)` → `_fail_step`.
- Sau thành công: `_score_versions` (QA tự chấm) và `_start_chain` (`params.$meta.then`).
- Khởi động lại: `StudioEngine.recover` đánh step đang chạy `FAILED` (code `INTERRUPTED`, retryable) và run `INTERRUPTED`; run không có step đang chạy (crash giữa 2 step) thì tự chạy tiếp. `lane_limits` kẹp theo `HARD_LANE_CAPS`.

## Recipe
- `engine/recipe.py::Recipe` / `StepSpec` / `Binding` / `ConcatBinding` — YAML → object; `${inputs.x}`, `in: {port: step.output}`, `map:` (chạy nhiều item), `rules`.
- `engine/recipe.py::substitute` · `coerce_value` · `apply_rules` — resolve tham số.
- `engine/library.py::RecipeLibrary` — recipe builtin + user (lưu/xoá qua API); `validate`, `lint`, `merge_text` (giữ comment khi lưu).
- `engine/kinds.py::describe_kinds` — schema param từng `kind` (`comfy_entries` đọc `workflows/index.json`) cho editor.
- `engine/exports.py::recipe_exports` — recipe này xuất được engine nào (từ tập kind).

## Lane + scheduler
- `contracts.py::Lane` = `GPU | BLENDER | CLOUD | CPU | NONE`.
- `engine/scheduler.py::LaneScheduler` — `acquire/release` theo `lane_limits`; ưu tiên `Waiter` theo `ready_at` + `set_priority`; **model affinity** (`model_group`) giữ model đang nạp, nhưng waiter chờ quá `AFFINITY_MAX_WAIT_S` (120s) thắng affinity; `hold/unhold` cho yield.
- `engine/gpu.py::GpuControl` — `yield_for(minutes)`, `resume`, `reprioritize`, `state` (event `gpu.updated`); `holders` = step đang chiếm lane yield.

## Gate (người duyệt)
- `engine/gates.py::PickGate` (`gate.pick`: chọn k ảnh) · `ApproveGate` (`gate.approve`) · `register_gates` · `is_gate` (prefix `gate.`).
- Mỗi gate: `awaiting_data`, `resolve(params, inputs, GateResolution)`, `auto_resolution` (khi `gate_enabled` tắt).
- `StudioEngine._open_gate` → run `awaiting_human`; `_auto_pick_by_qa` dùng `engine/autopick.py::pick_by_qa` (tự chọn ảnh QA cao nhất).
- Invariant: gate `Lane.NONE`, không cacheable; `resolve_gate` mới đẩy run chạy tiếp.

## Cache + seed
- `engine/cache.py::cache_key` · `executor_cache_key` — key = kind + version + params (đã `normalize_params`, bỏ param điều khiển) + inputs + material; `None` = không cache. `cacheable_meta` lọc meta lưu kèm.
- `contracts.py::cache_enabled` — param `cache` mặc định bật; step Claude cùng prompt/inputs/model dùng lại câu trả lời.
- `engine/seeds.py::seed_used` · `pin_overrides` · `override_seed_keys` — giữ/đổi seed khi rerun (`rerun_run(seed="new"|"same")`).

## Chain + Flow (workflow = graph recipe)
- `engine/chain.py::parse_chain` · `map_inputs` · `published_outputs` — `params.$meta.then`; `chain_runs` ghi ngược để không start 2 lần.
- `engine/flows.py::FlowEngine` — `start`, `advance`, `retry_node`, `cancel`, `recover`; mỗi node = 1 run thường gắn `$meta.workflow_run_id/node_id`; trạng thái node suy ra từ run con (`on_run_status`), restart core chạy lại cùng `advance`.
- `engine/flows.py::FlowLibrary` · `validate_def` · `flow_deps` · `downstream` — builtin `flows/*.yaml` + user flow; kiểm vòng (`_cycle`), `FlowInvalid` / `FlowNotFound` / `FlowStateError`.
- `engine/flow_batch.py::FlowBatches` — chạy 1 flow × N hàng input.
- `engine/manifest.py::ManifestRow` · `rows_from_proposal` · `to_csv/from_csv/to_yaml/from_yaml` — danh sách asset (asset list) của project, trạng thái hàng `row_status`.

## Project + style + QA
- `engine/projects.py::apply_style` — chèn prefix/suffix style của project vào prompt; `strip_style` gỡ ra khi rerun.
- `engine/style_bible.py::StyleBible` · `lock_bible` · `pick_refs` · `aggregate` — style bible từ asset đã duyệt; `style_ref_input`, `is_visual`.
- `engine/qa.py::score_image` · `score_tile` · `QaContext` — chấm QA ảnh (numpy, không model).
- `engine/project_qa.py::measure` · `Measure` · `pick_versions` · `palette_from_image` — đo asset toàn project.
- `engine/similar.py::dhash` · `phash` · `duplicate_groups` · `cosine_topk` — trùng/tương tự; `engine/image_index.py::ImageIndex` (CLIP embed qua `Embedder`).
- `engine/auto_export.py::AutoExporter` — duyệt version xong tự chép ra thư mục game của project (`_place`).

## Invariant thấy trong code/test
- Step mapping qua rỗng (nhánh upstream tắt) → `SUCCEEDED` với outputs `{}`, không chiếm lane, không boot ComfyUI.
- Output blob phải còn tồn tại (`_blobs_present`) mới tính cache hit.
- Test: `core/tests/test_engine_runner.py`, `test_p2_scheduler.py`, `test_gpu_yield.py`, `test_chain.py`, `test_w9_flows.py`, `test_perf_lanes_cache.py`, `test_rerun_seed.py`, `test_auto_pick_seeds.py`.
