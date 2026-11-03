## WP-12N: NewRun — preset người dùng, biến thể theo seed, tự chọn ảnh QA cao nhất (audit W3, W4, W5)
- Mục tiêu:
  1. Preset người dùng: "Lưu thiết lập này" (tên) trong NewRun; danh sách preset theo recipe (+ project nếu đang chọn project); chọn → điền form; xoá preset. Core: bảng SQLite `run_presets` (id, name, recipe_id, project_id nullable, inputs json, params json, created_at) migration idempotent + API CRUD `/api/presets/runs` (tránh đụng route preset style có sẵn — kiểm tên).
  2. Batch "1 prompt × N seed": varyBy='seeds' cạnh mode batch hiện có (NewRunView:642-646), N 2–16, seed tăng dần từ seed gốc (hoặc ngẫu nhiên nếu -1) → dùng cơ chế batch có sẵn, mỗi lượt override seed của mọi step có param seed (tham khảo engine/seeds.py commit 56a6ca2).
  3. Công tắc "Tự chọn ảnh điểm QA cao nhất" (tắt mặc định) khi chạy: gate pick tự resolve bằng ứng viên QA cao nhất (gate_policy/auto_first có sẵn ở MCP — mcp/server.py, tìm logic chung trong engine), kèm cảnh báo ngắn.
- Phạm vi: core store (db.py migration, repo.py presets), api module mới core/studio_core/api/run_presets.py (+ đăng ký router — app.py/routes chỗ đăng ký: sửa tối thiểu), engine chỉ phần auto-pick nếu cần (ghi rõ file), tests/test_run_presets.py + test auto-pick; app views/NewRunView.vue, stores/catalog.ts (nếu cần), components mới (vd RunPresetMenu.vue), api/mock/server.ts (chỉ presets/batch seeds). | Cấm: api/types.ts + api/client.ts (ghi patch "Hot file cần thêm" — tạm kiểu cục bộ), mcp/** (WP-12M), GalleryView/Flow*/Audit/Restyle/CommandPalette (WP-12FX), RunDetailView.
- Skill: web-design. Model: sonnet.
- Tiêu chí:
  - [ ] pytest: CRUD presets, migration 2 lần, preset project bị xoá; batch seeds tạo N run với seed khác nhau (executor fake); auto-pick chọn đúng ứng viên QA cao nhất, hoà điểm → ứng viên đầu, không có điểm QA → ứng viên đầu + warning.
  - [ ] typecheck sạch.
  - [ ] Ảnh mock NewRun: menu preset mở, dialog lưu, varyBy seeds, công tắc auto-pick (light+dark) → docs/qa/r12/WP-12N/.
- Nhìn thấy được: có.
- Báo cáo: `.planning/wp/WP-12N-report.md`
## Nhật ký
- 09:26 Core xong: run_presets (db/repo/api), engine/autopick.py + hook runner._open_gate, seed_override trong start_run; 17 test mới + test liên quan pass
- 09:30 UI xong: RunPresetMenu.vue, api/runPresets.ts, NewRunView (varyBy seeds, auto-pick), mock presets; typecheck sạch. Còn: chụp ảnh
- 09:38 Ảnh xong (docs/qa/r12/WP-12N, light+dark), vite tắt theo PID, typecheck+pytest xanh
