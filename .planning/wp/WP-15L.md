## WP-15L: "Gửi tới…" — các chức năng gọi sang nhau
- Mục tiêu: người dùng chuyển kết quả giữa các màn không phải chép đường dẫn/chọn lại.
  1. Component dùng chung `SendToMenu.vue` (nút "Gửi tới…" + menu, bàn phím đủ: Enter/Esc/mũi tên, focus trả về nút) nhận danh sách đích hợp lệ theo loại đầu vào (version ids | file paths | project id). Đích:
     - NewRun recipe gợi ý (tái dùng composables/followUp.ts followUps + prefillQuery, không viết lại logic).
     - Restyle (prefill project + danh sách version/file qua query).
     - Audit (prefill thư mục/project).
     - Đóng gói Pack (mở PackDialog với version đã chọn).
     - Gallery (lọc theo project / làm nổi version).
  2. Gắn menu: Gallery (thanh chọn nhiều), RunDetail (output), Restyle kết quả (→ Gallery xem bản mới, → Pack), Audit (finding/file → Restyle các ảnh này, → Gallery nếu file thuộc version thư viện, → nhập thư viện qua API import có sẵn), ProjectDetail.
  3. Đích nhận query: RestyleView, AuditView, GalleryView đọc query prefill (kiểu NewRunView.vue:248-256 đọc rồi router.replace xoá query).
  4. Command palette (components/CommandPalette.vue): thêm hành động "Chạy lại run gần nhất", "Mở Audit/Restyle/Gallery cho project hiện tại", "Duyệt cổng đang chờ"; sửa bảng phím tắt (:170-187) cho khớp mảng NAV ở App.vue:34-40; xác nhận Ctrl+Enter chạy run có handler, không có thì bỏ khỏi bảng hoặc thêm handler ở NewRun.
  5. Sửa link clone recipe RecipesView.vue:108 `/recipes/new?from=` (không có route /recipes/new, khớp nhầm /recipes/:id) — kiểm RecipeEditorView xử lý id 'new'+from; nếu không thì sửa đúng. Kiểm FlowEditorView có đọc `from` (FlowsView.vue:154).
- Phạm vi: app/src/renderer/src (component mới, các view trên, composables). Cấm hot file api/types.ts, api/client.ts, api/mock/server.ts → ghi "Hot file cần thêm". Không chạm core trừ khi thiếu endpoint thật (ghi báo cáo).
- Model: sonnet. Không build. `cd app && npm run typecheck`; test vitest nếu project có (kiểm package.json).
- Nghiệm thu: mỗi đích ở mục 1 đi được từ ≥ 1 màn và màn đích nhận đúng prefill; Audit có link đi ít nhất Restyle + Gallery; palette có ≥ 3 hành động mới; bảng phím tắt khớp NAV; clone recipe mở đúng editor có dữ liệu nguồn; typecheck sạch.
- Nhìn thấy được: có — chạy app thật (cách chạy nhật ký .planning/wp/WP-13QA.md), ảnh docs/qa/r15/WP-15L/.
- Báo cáo: `.planning/wp/WP-15L-report.md`
## Nhật ký
- Bổ sung (sau WP-15S 62a722f): BundleMenu.vue / PackDialog cho chọn shader khi đích Android (Compose) / iOS (SwiftUI) — core đã nhận cờ `shaders` cho android/ios (store/bundle.py export_engine, store/pack.py _export_one); hint ghi "Android 13+ / iOS 17+".
- AuditView: nhận cancelled -> tắt applying/plan ngay; rescan_needed -> quét lại; thêm rescan_needed vào auditTypes.ts
- Thêm composables/sendTo.ts (sendItems, useQueryPrefill), components/SendToMenu.vue, PackDialog prop preselect; Gallery/Restyle/Audit nhận query
- Gắn SendToMenu: Gallery selbar, RunDetail hero, Restyle kết quả, Audit fixbar (+linkedProject theo auto_export_dir), ProjectDetail; palette +4 hành động; bảng phím sửa; BundleMenu hint shader android/ios; typecheck sạch; đang chụp ảnh (vite mock cổng 5287)
