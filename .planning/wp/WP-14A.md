## WP-14A: Polish điều hướng node, sửa preset, ResizeObserver
- Mục tiêu: (1) FlowCanvas: mũi tên chọn node gần nhất nằm trong nón ±45° theo hướng bấm; không có thì đứng yên (QA r13: Phải nhảy sang node dưới). (2) RunPresetMenu: đổi tên preset + "Ghi đè bằng thiết lập hiện tại" — core thêm `PATCH /api/presets/runs/{id}` (name, inputs, params) trong api/run_presets.py + repo; client.ts/types: ĐƯỢC sửa đúng chỗ preset (thêm updateRunPreset) vì main giao rõ. (3) Console "ResizeObserver loop completed with undelivered notifications" (QA r12/r13 ở #/gallery, #/runs/<id>): tìm nơi ResizeObserver đổi layout đồng bộ, gom qua requestAnimationFrame.
- Phạm vi: components/flow/FlowCanvas.vue, components/RunPresetMenu.vue, api/runPresets.ts, api/client.ts (chỉ preset), api/mock/server.ts (chỉ preset), core/studio_core/api/run_presets.py, store/repo.py (chỉ preset), tests/test_run_presets.py, file dùng ResizeObserver gây lỗi (ghi rõ). | Cấm: core scan_jobs/audit/qa (WP-14B), ops/lut.py (WP-14C), AuditView/RestyleView/ProjectDetail.
- Skill: web-design. Model: sonnet.
- Tiêu chí: pytest preset pass (+test PATCH, tên trùng/rỗng); typecheck sạch; Playwright mock: mũi tên không nhảy xa (ảnh), đổi tên preset (ảnh light+dark); console #/gallery + #/runs/<id> 0 ResizeObserver error → docs/qa/r14/WP-14A/.
- Báo cáo: `.planning/wp/WP-14A-report.md`
## Nhật ký
- core PATCH presets + FlowCanvas cone + test viết xong
- client/types/mock/UI preset + PageHeader rAF xong, typecheck ok
- QA xong, báo cáo ghi
