## WP-15QA — DONE
- Mục: 1 preset ĐẠT (+1 lỗi nhẹ 409 EN) · 2 Flow mũi tên nón ±45° ĐẠT (24/24) · 3 ResizeObserver 0× ĐẠT (r13: 3×) · 4 Audit fix huỷ giữa chừng + Hoàn tác (hash khớp) ĐẠT · 5 Project QA huỷ ĐẠT
- Lỗi: toast 409 đổi tên preset trùng là tiếng Anh thô "a preset named 'X' already exists" (core/studio_core/api/run_presets.py:91, RunPresetMenu.vue:119); client chặn trùng bằng "Đã có thiết lập trùng tên." nên chỉ lộ khi list cũ.
- Quan sát: audit fix huỷ → toast trễ ~10 s (re-scan trong request fix), thanh lúc đó "Đang chuẩn bị…"; backup huỷ dở ghi gpu 408→27 MB tính cả phần không ghi (core/studio_core/export/audit.py ~946-948).
- Nhìn: docs/qa/r14/QA/ (31 png + RESULT.md)
- Dọn: PID 44328,42796,3336,26208,44412,37724,44036,35284,33724,44636,44528,44624 đã tắt; xoá scratch/qa15-* (qa15f-* của WP khác giữ nguyên).
