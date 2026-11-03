## WP-17B — DONE
- Đã đổi: components/SendToMenu.vue (defineExpose show), gate/gateUtil.ts (candidateSource, isSendKey), gate/GatePicker.vue + GateApprove.vue (nút "Gửi tới…" ở footer cho ứng viên đang focus, phím S, hint, footer wrap), Lightbox.vue (prop sendFor: nút + phím S), CommandPalette.vue (1 dòng bảng phím S).
- Tiêu chí: ứng viên bị loại/chưa chọn vẫn gửi được (blob còn trong kho; Gallery bị omit vì chưa là version); S không đè phím có sẵn.
- Review: S bị chặn khi gõ input/select (isTypingTarget), palette/shortcuts mở, lightbox mở (Picker/Approve return; Lightbox tự xử S, bỏ qua khi target trong role=menu), đang sending; cổng đã duyệt thì component bị gỡ nên không còn handler. Ctrl/Alt/Meta+S không bắt.
- Test: npm run typecheck sạch phần của WP-17B (còn 1 lỗi mock/server.ts PackCandidate.engines thuộc WP-17A).
- Nhìn: docs/qa/r17/WP-17B/01 approve S, 02 picker S, 03 lightbox S (vite mock cổng 5297, đã tắt theo PID).
- Hot file cần thêm: không. Importer từ blob chưa có đường dẫn nên "Nhập thư viện" không hiện ở cổng (cần importPaths).
- Commit message: feat(gates): "Send to…" menu and S shortcut for gate candidates and lightbox
