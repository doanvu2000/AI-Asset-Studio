## WP-16C — DONE
- Đã đổi (app): composables/sendTo.ts (đích 'flow', flowTargets, useQueryPrefill hỗ trợ khoá `input.*`), components/SendToMenu.vue (nạp flows, nhóm flow cùng nhóm recipe), views/FlowStartView.vue (prefill input.<name>/project, kiểm fits + toast khi hash rác), views/FlowRunView.vue (SendToMenu cho output node đã xong), components/CommandPalette.vue (mục "Chạy flow <tên>").
- Tiêu chí: Gallery chọn ảnh -> Gửi tới -> Chạy flow -> form có ảnh sẵn OK; FlowRun gửi tới Restyle/Thư viện OK (Pack chỉ hiện khi run thuộc dự án); palette có flow OK; typecheck sạch.
- Review: flow không có input blob khớp thì không hiện; flow bị xoá -> trang "Không mở được workflow"; hash rác/input lạ/project lạ -> toast hoặc bỏ qua, query vẫn bị xoá; ArrowUp/Down+Enter dùng được.
- Test: npm run typecheck sạch (không build, không vitest).
- Nhìn: docs/qa/r16/WP-16C/01-05 (mock vite cổng 5291, tạo flow ảnh bằng api().createFlow trong trang vì mock built-in chỉ có input text).
- Hot file cần thêm: không. Gợi ý: mock/server.ts thêm 1 flow built-in có input image để QA khỏi phải tạo tay.
- Commit message đề xuất: feat(app): Send to > Run flow with prefilled blob input, flow output Send to menu, Ctrl+K run flow entries
