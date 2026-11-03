## WP-13QA — DONE
Kết quả từng mục: (1) Lịch sử ⋯ seed mới/giữ seed ĐẠT; (2) Flow editor chọn dây/Delete/Ctrl+Z/Tab/mũi tên/Enter ĐẠT; (3) Audit 601/4001 + Huỷ ĐẠT, Restyle 340/4000 + Huỷ ĐẠT, Gallery QA 293/500 + Huỷ ĐẠT; (4) normal AI 4,0/1,7/1,6 s, worker tự tắt idle ~120 s, đóng app không còn worker (PID kiểm) ĐẠT; (5) console sạch.
Lỗi: L1 nhãn "Chấm điểm N ảnh cũ" không giảm: app/src/renderer/src/composables/qa.ts:7 QA_VERSION=1 lệch core/studio_core/engine/qa.py:20 QA_VERSION=2 (dùng ở GalleryView.vue:203-208). Quan sát: thanh Audit dừng ~318/409 khi sang phase analyze; huỷ trả 409 → console "Failed to load resource".
Console: 3× ResizeObserver vô hại, 2× 409 do huỷ, không pageerror.
Ảnh: docs/qa/r13/QA/ (35 png) + RESULT.md. Dọn xong (PID, scratch/qa13-*). CHƯA KIỂM: normal AI method=ai kiểm meta (chỉ thấy tốc độ/UI), focus ring node đang selected.
