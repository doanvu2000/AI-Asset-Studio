## WP-19B: Ctrl+K "Gửi gần đây"
- Mục tiêu: mỗi lần Gửi tới thành công (điều hướng/mở dialog trong composables/sendTo.ts / SendToMenu.vue choose) ghi 1 mục {target, label đích, label nguồn (tên asset/ảnh), route to (path+query), at} vào localStorage key `sendto:recent` (tối đa 8, mới nhất trước, bỏ trùng to). Mọi đọc/ghi bọc try/catch; không có storage vẫn chạy. CommandPalette nhóm "Gửi gần đây" (khi query rỗng hoặc khớp nhãn) → chạy lại router.push(to). Đích mở ra báo tệp không còn (sentFileError) → gỡ mục đó (sự kiện/hàm dùng chung trong sendTo.ts). Dialog đích (import/pack) không lưu vào recent (không có route) — ghi rõ.
- Phạm vi: app composables/sendTo.ts, components/SendToMenu.vue, CommandPalette.vue. Cấm LibraryImportDialog (19A), RunsView/RunDetail/CompareView (19C), hot file.
- Model: sonnet. Không build. typecheck. Ảnh docs/qa/r19/WP-19B/ (vite mock, cổng riêng, tắt theo PID).
- Báo cáo: `.planning/wp/WP-19B-report.md`
## Nhật ký
- AUDIT: sendTo.ts/SendToMenu/CommandPalette đọc xong; sentFileError nằm ở views nên gỡ mục stale qua "blame" trong sendTo.ts (không sửa views).
- CODE: recentSends/recordRecent/removeRecent/markSent trong sendTo.ts; SendToMenu.choose ghi mục; CommandPalette nhóm "Gửi gần đây".
- TEST: typecheck sạch; vite mock cổng 5319 (đã tắt): ghi/đọc/lọc/corrupt ổn, ảnh docs/qa/r19/WP-19B/.
