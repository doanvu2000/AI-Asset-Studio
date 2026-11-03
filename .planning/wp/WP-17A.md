## WP-17A: Dọn nợ UX lượt 16
- 1. RestyleView, NewRunView: prefill hash rác/hết hạn → toast VN qua sentFileError (composables/sendTo.ts, xem FlowStartView sau c4e50ea).
- 2. Core GET pack listing (core/studio_core/api/pack.py ~132-145): thêm `engines: string[]` mỗi asset (engine_ready chung → mọi ENGINES; chỉ ảnh tĩnh → ["android","ios"]; không gì → []), giữ `engine_ready` cũ (test). PackDialog hiện đúng (vd nhãn "chỉ Android/iOS" thay "chưa xuất được").
- 3. Mock (api/mock/server.ts — gói này giữ): thêm flow built-in có input ảnh + catalog shader dùng id thật của core (dissolve, hit_flash, outline_pulse, palette_swap, water_ripple, wind_sway).
- Phạm vi: app views/RestyleView.vue, NewRunView.vue, components/PackDialog.vue, api/types.ts (PackAsset engines), api/mock/server.ts; core api/pack.py + test. Cấm: GatePicker/Gates (17B), ShaderPicker/shader_preview (17C), audit_flutter (17D), client.ts, mcp/server.py.
- Model: sonnet. Không build. pytest song song + typecheck. Ảnh docs/qa/r17/WP-17A/ (mock).
- Báo cáo: `.planning/wp/WP-17A-report.md`
## Nhật ký
- [17A] core pack.py engines + test_pack, types.ts, RestyleView/NewRunView sentFileError
- [17A] PackDialog assetNote, mock catalog 6 shader + flow sprite-lighting (flows.ts) + packCandidates.engines
- [17A] chụp xong docs/qa/r17/WP-17A, tắt vite
