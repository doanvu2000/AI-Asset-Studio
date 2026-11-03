## WP-19QA — DONE
Chi tiết: docs/qa/r19/QA/RESULT.md (+ ảnh 20-74). Electron thật + core thật, CDP 9364, data scratch/qa19-*.
1. 7839c8e: ĐẠT — Lightbox Thư viện ← → rồi C mở So sánh với ảnh vừa xem (cả asset nhiều bản); Gửi tới → Đóng gói / Nhập vào thư viện, đóng bằng Huỷ/Esc/Nhập → menu không mở lại.
2. WP-19A: ĐẠT — mặc định "Mỗi tệp một asset" 3 file → 3 asset; "Gộp"+tên → 1 asset 3 phiên bản; trùng stem → "kappa 2".
3. WP-19B: ĐẠT — "Gửi gần đây" có nhãn+nguồn, lọc, chạy lại đúng route (Restyle/NewRun/Flow/Audit), mục hỏng bị gỡ + toast, nhớ qua tắt/mở app.
4. WP-19C: ĐẠT — chọn 2 (checkbox, Shift-click, tối đa 2), diff chỉ dòng khác / Hiện tất cả / So sánh ảnh kết quả / Esc lồng; RunDetail "So sánh với…"; ui-juice seed 99 vs 7 → dòng seed khác; run chờ duyệt → cảnh báo.
5. Console: ĐẠT — renderer.log 1 WARN cố ý, 0 ERR, 0 ResizeObserver; core.log 0×5xx (1 ConnectionResetError lúc kill để kiểm restart).
Lỗi: không có lỗi chặn. Nhẹ: focus về body sau khi dialog đóng (SendToMenu.vue:34-36,146-147); chạy lại mục recent không đẩy lên đầu (sendTo.ts:147); RunDiff input hash trần hiện chuỗi (RunDiff.vue:99-101); ui-icon-variants không có seed (brief lệch).
Nhìn: docs/qa/r19/QA/22,25,33,36,46,47_*,49,62,64,65,72,74.
Dọn: tắt theo PID 34536 (npx → electron-vite → electron + core), xoá scratch/qa19-* + app/scratch/qa19-cfg, cổng 5173/9364 trống.
CHƯA KIỂM: run failed trong RunDiff, >8 mục recent, localStorage bị chặn.
