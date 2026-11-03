## WP-12B: Chạy lại + seed (audit W1, W2)
- Mục tiêu:
  1. Core ghi seed THỰC dùng của mỗi step có seed vào step.meta (`seed_used`), kể cả khi seed=-1/ngẫu nhiên được rút. Tìm điểm chung trong engine (runner/kinds) thay vì sửa từng kind; ComfyUI executor (adapters/comfy/executor.py:109 — nơi quyết định cache khi không pin seed) đọc đúng seed đã rút.
  2. API "chạy lại": `POST /api/runs/{id}/rerun` body `{seed: 'new' | 'same'}` → tạo run mới cùng recipe/inputs/params/project; `same` = ép mọi step seed bằng seed_used của run cũ (params override), `new` = như cũ (seed mới). Mặc định `new`. Hành vi cache mặc định KHÔNG đổi (quyết định chờ chủ nhân: không tự pin seed).
  3. RunDetailView header: nút "Chạy lại" (seed mới) + menu "Chạy lại, giữ seed"; điều hướng sang run mới. StepInspector hiện seed_used + nút sao chép.
  4. GatePicker "tạo lại" (GatePicker.vue:82) giữ như cũ (seed mới).
- Phạm vi: core/studio_core/engine/runner.py, engine/kinds.py (chỉ phần seed chung), adapters/comfy/executor.py (chỉ đọc seed), api/routes.py (endpoint rerun) hoặc module api runs, tests/test_rerun_seed.py; app views/RunDetailView.vue, components/flow/StepInspector.vue, api/mock/server.ts (chỉ rerun mock). | Cấm: api/types.ts + api/client.ts (ghi patch "Hot file cần thêm" — main gộp), mcp/server.py (ghi đề xuất tool `rerun`), GalleryView.vue, FlowCanvas.vue, FlowEditorView.vue, FlowStartView.vue (WP-12FX), ops/lut.py (WP-12P1).
- Model: sonnet.
- Tiêu chí:
  - [ ] pytest: step có seed -1 → meta seed_used là số; rerun same → run mới có seed y hệt mọi step (fake executor); rerun new → seed khác; run không có step seed → rerun vẫn OK; run cũ không có seed_used (dữ liệu trước) → `same` báo lỗi rõ 409 hoặc fallback seed trong params, ghi trong báo cáo.
  - [ ] Cache: test xác nhận rerun `same` với executor fake dùng lại cache (hash giống), `new` không.
  - [ ] `npm run typecheck` sạch (kiểu tạm cục bộ nếu cần, ghi patch).
  - [ ] Ảnh mock RunDetail có nút Chạy lại + menu, StepInspector seed (light+dark) → docs/qa/r12/WP-12B/.
- Nhìn thấy được: có.
- Báo cáo: `.planning/wp/WP-12B-report.md`
## Nhật ký
- 09:13 core: engine/seeds.py + runner seed_used + rerun_run + POST /runs/{id}/rerun viết xong
- core test_rerun_seed 13 pass + hồi quy engine
- app: RunDetailView (rerun split button + seed row), composables/rerun.ts, mock rerunRun viết xong
- xong: QA ảnh, báo cáo WP-12B-report.md
