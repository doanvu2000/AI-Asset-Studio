## WP-12NI: Cài model normal AI (MoGe-2) từ app như các pack khác
- Mục tiêu: pack tuỳ chọn "normal-ai" (Normal map AI, CPU, ~135 MB + torch CPU) xuất hiện ở Settings/bootstrap như pack `tts`, `similar` hiện có: có `hw` (CPU, min_ram_gb 4 → đèn xanh), trạng thái đã cài/chưa, nút cài chạy logic của setup/normal-ai.ps1 (commit 1fa8db5) hoặc port sang Python trong bootstrap (ưu tiên tái dùng cơ chế pack có venv riêng nếu có, vd tts/similar cài thế nào). Đường dẫn cài theo Settings data dir/models (không hard-code L:\ — mặc định hiện L:\AI\setup\normal-ai giữ làm fallback nếu đã có). adapters/normal_ai.py tìm model theo đúng nơi pack cài. Ghi license MIT/Apache vào manifest như pack khác (nếu có field) + AI credits.
- Phạm vi: core/studio_core/bootstrap/** (manifest.json + code cài), core/studio_core/adapters/normal_ai.py (đường dẫn), core/studio_core/hardware.py (nếu fit cần biết pack), setup/normal-ai.ps1, tests (tests/test_bootstrap*.py hoặc mới). App: chỉ khi Settings không tự hiện pack từ manifest — ghi lại, không sửa nếu không cần (nếu cần sửa app → ghi "Ngoài phạm vi").
  Cấm: app/src/renderer/src/views/NewRunView.vue, core store/db.py, store/repo.py, api/run_presets.py, engine/runner.py, engine/autopick.py (WP-12N đang làm), api/types.ts, client.ts, mcp/**.
- Model: sonnet.
- Tiêu chí:
  - [ ] /api/fit (hoặc API pack list) hiện pack normal-ai, hw xanh trên máy CPU-only giả lập.
  - [ ] Cài thật trên máy này vào thư mục test trong scratchpad hoặc trên L: (KHÔNG trên C:) chạy được, adapter dùng được (1 ảnh normal AI qua op), hoặc nếu đã có L:\AI\setup\normal-ai thì nhận diện "đã cài" không tải lại.
  - [ ] Test cài mock (không tải mạng) + test nhận diện đã cài/chưa.
  - [ ] pytest bootstrap/hardware/normal liên quan pass.
- Báo cáo: `.planning/wp/WP-12NI-report.md`
## Nhật ký
- B1 audit: manifest (pack normal-ai + cấu hình top-level normal_ai), plan.component_*, hardware.pack_states, job._normal_ai (venv uv + torch CPU + zip MoGe b942f00 + snapshot_download), adapter.locate(settings, models_dir) đã code. UI SetupPanel tự render pack từ plan -> không cần sửa app.
- B2 test mock: tests/test_normal_ai_install.py (8 test) + test_setup/hardware_fit/normal_ai/normal2d = 56 passed. Tiếp: cài thật vào L:\AI\setup\_ni test (đường dẫn có khoảng trắng)
- B3 cài thật lần 1 lỗi: uv không nhận URL codeload không đuôi .zip -> sửa: tải về tmp/*.zip rồi cài. Chạy lại.
- B4 cài thật OK (47s, 419MB tính), adapter chạy 1 ảnh 5.8s, pytest 56+ pass
