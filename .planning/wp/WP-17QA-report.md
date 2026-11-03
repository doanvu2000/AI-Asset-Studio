## WP-17QA — DONE (Electron thật + core thật, không GPU)
Chi tiết + ảnh: docs/qa/r17/QA/RESULT.md (29 png). 
1. Audit plan_id: Flutter 349 MB Ghi 62,7 s (không plan_id) → 39,0 s (có plan_id); sửa file giữa chừng → 73,0 s (lập lại); Godot 305 MB không khác (4,4 s, plan chỉ sửa .import); undo khớp. Android ảnh tĩnh: icon / icon1024 sạch.
2. 17A: hash rác → toast VN; PackDialog nhãn "chỉ Android/iOS"; shader preview dùng blob asset đầu tiên chọn (đổi theo tick). ĐẠT.
3. 17B: S + nút Gửi tới… ở cổng chọn (recipe thử qa17-pick) và cổng duyệt ui-juice; NewRun/Restyle/Chạy flow nhận đúng ảnh; S bị chặn khi gõ/palette/Ctrl/Alt; Lightbox cổng duyệt S ĐẠT. CHƯA KIỂM: lightbox cổng chọn (không mở được).
4. 17C: BundleMenu thumbnail = sheet.png của step. ĐẠT.
5. 17D: nhãn VN font thiếu/chưa khai báo/WAV nặng, sửa font + quét lại + Hoàn tác (md5 khớp). ĐẠT.
6. Console: 0 ResizeObserver, 0 ERR, 0 pageerror, core 0×5xx.
Lỗi: GatePicker.vue:25,185 lightbox không bao giờ được gán (mã chết, nhẹ).
Dọn: tắt theo PID, xoá scratch/qa17-*.
