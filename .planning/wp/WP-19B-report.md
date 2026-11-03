## WP-19B — DONE
- Đã đổi: composables/sendTo.ts (recentSends, recordRecent, removeRecent, markSent; sentFileError gỡ mục vừa gửi nếu trong 15s), components/SendToMenu.vue (choose ghi recent + tên nguồn), components/CommandPalette.vue (nhóm "Gửi gần đây": 4 mục khi rỗng, 8 khi gõ, khớp nhãn/nguồn; chạy lại router.push).
- Tiêu chí: key `sendto:recent`, tối đa 8, mới nhất trước, bỏ trùng theo (path+query); đọc/ghi try/catch, JSON hỏng -> danh sách rỗng; dialog Pack/Import không có route nên không lưu. Tệp không còn -> sentFileError gỡ mục (không cần sửa views).
- Test: npm run typecheck sạch. Mock: gửi từ trang dự án -> localStorage có mục, Ctrl+K hiện nhóm, lọc theo nhãn đúng, JSON hỏng không lỗi. Gỡ mục stale qua sentFileError chưa chạy E2E (mock blobInfo không lỗi), chỉ review code.
- Nhìn: docs/qa/r19/WP-19B/01_menu.png, 02_palette_recent.png, 03_seeded.png (vite mock 5319, đã tắt theo PID).
- Hot file cần thêm: không.
- Commit: feat(palette): "Recent sends" group in Ctrl+K that replays the last 8 Send-to routes, dropping entries whose file is gone
