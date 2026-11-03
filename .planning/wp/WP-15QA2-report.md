## WP-15QA2 — PARTIAL
- Mục: 1 Gửi tới ĐẠT (+L2 focus version không chọn) · 2 Ctrl+K ĐẠT (+L3 409 EN thô khi chạy lại lượt đang chờ; Ctrl+1..8 khớp) · 3 Pack Android+iOS+shader ĐẠT với sprite sheet, LỖI với PNG nhập (500) · 4 Audit Flutter quét/sửa/quét lại/hoàn tác (hash khớp) + huỷ <2 s ĐẠT · 5 Console: LỖI nhẹ 18× ResizeObserver ở /gallery, 0 pageerror.
- Lỗi: core/studio_core/store/pack.py:974 (work không được tạo khi mọi asset bị skip → FileNotFoundError provenance.json, 500) · app/.../views/GalleryView.vue:462-482 (focus không chọn) · components/CommandPalette.vue:63-71 (rerun lượt đang chờ → 409 EN) · views/AuditView.vue:190 ("0 file chưa sửa" khi huỷ sớm).
- Quan sát: AGSL nhúng trong .kt (không .agsl riêng); Android/iOS nhận cả 6 shader; nhãn "Đã sửa" chạy trong lúc nén backup (~87 s/560 MB); Xem trước 122 ảnh ~75 s chỉ spinner.
- Nhìn: docs/qa/r15/QA/ (png + RESULT.md).
- Dọn: PID 38188,34396,28560,29772,37756,42316,23380,22476 đã tắt; xoá scratch/qa15q-*.
