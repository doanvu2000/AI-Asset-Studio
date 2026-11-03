## WP-18QA — PARTIAL (Electron thật + core thật, 2026-11-03, cửa sổ 1320x852, sáng, không GPU/ComfyUI)
Cách chạy: `electron-vite dev -c scratch/qa18-cfg/electron.vite.config.ts -- --remote-debugging-port=9363 --user-data-dir=scratch/qa18-ud` (cwd app/, vite bind 127.0.0.1:5173 vì [::1]:5173 bị project khác chiếm), STUDIO_DATA_DIR=app/scratch/qa18-data, điều khiển bằng CDP WebSocket thô (máy không có Playwright). Lưu ý môi trường: cửa sổ có lúc `visibilityState=hidden` làm Input.dispatchMouseEvent treo; bật Emulation.setFocusEmulationEnabled thì ổn. Đã tắt theo PID và xoá app/scratch/qa18-*.

### Kết quả từng mục
1. WP-18A (Audit Flutter ~300 MB) — ĐẠT. Project tạm 22 PNG nhiễu 2400x1800 = 282 MB.
   - Xem trước 7,8 s (r17: 25-28 s với 349 MB); câu "Sẽ sửa 22 mục · ảnh chuyển WebP 282 MB → ~37.7 MB (ước tính)" có nhãn ước tính (02).
   - Ghi 12,6 s (r17: 39-63 s); thanh "Đã sửa N/22 tệp" chạy ngay, số thật sau ghi 39,9 MB (41 861 796 B) vs ước tính 37,7 MB: lệch -5,5% (03). Hoàn tác -> md5 thư mục assets khớp trước (04).
   - Ghi lần 2 + Huỷ ở 11/22: toast "Đã dừng: ghi 13 file, 9 file chưa sửa · hoàn tác được ở backup …" sau 0,67 s (05); Hoàn tác "Đã khôi phục 26 file", md5 cả cây và assets khớp (06).
2. WP-18B (ui-icon-variants + Nhập vào thư viện) — ĐẠT.
   - Recipe CPU (ảnh 512x512 alpha, 6 biến thể hue) chạy tới cổng chọn (10).
   - Phím 3 chọn ô 3 (11); 5 chọn ô 5, bấm lại bỏ chọn (toggle); 9/0 không đổi (chỉ 6 ứng viên) (13); V mở Lightbox ứng viên focus (icon_base_hue300.png 512×512 6/6, nút So sánh/Gửi tới…) (12).
   - S mở menu Gửi tới, mục cuối "Nhập vào thư viện · icon_base_hue060.png" (14) -> dialog chọn dự án + "Đánh dấu đã duyệt" (15) -> toast "Đã nhập 1 file vào thư viện (đã duyệt)" (16); Thư viện có asset mới icon_base_hue060 (vàng/sao tím) "vừa xong", Đã duyệt (20).
   - Bảng phím "?" có dòng "V Xem lớn (cổng chọn ảnh)" cạnh S (18).
3. WP-18C (So sánh) — PARTIAL. Thư viện chọn icon1024 (1024²) + icon256 (256²):
   - Trượt mặc định, thanh 660->787 px sau →→ rồi 724 sau ← (5%/lần) (22); phím 1/2/3 đổi kiểu; Cạnh nhau A 556 px, B 139 px = cùng scale (23); Nháy tự đổi ~600 ms (24), Space đổi A/B và dừng tự nháy, nút "Nháy tự động" bật lại; Đổi chỗ đổi nhãn A/B.
   - Zoom/pan đồng bộ: wheel trên A 556->899 px và B 139->225 px (cùng hệ số 1,617), kéo (+96,+48) trên A -> cả hai dịch đúng, wheel trên B cũng đồng bộ (25); phím 0 về 54% vừa khít; Esc đóng.
   - Lightbox C: trên cổng chọn (V -> C) mở So sánh với ảnh trước (vòng), Esc đóng compare rồi lightbox (27) — ĐẠT. Trong Thư viện: LỖI (xem dưới) (26).
4. Console — ĐẠT: renderer.log 0 WARN/ERR, 0× ResizeObserver; phiên CDP cuối (compare + cổng + Lightbox) 0 exception/console error/warning; core.log 0 phản hồi 5xx (log chỉ có dòng khởi động/WebSocket), 0 traceback.

### Lỗi / quan sát
| # | Mức | Mô tả | Vị trí |
|---|-----|-------|--------|
| 1 | vừa | Lightbox trong Thư viện: phím C không làm gì và không có nút So sánh (kể cả asset nhiều bản, vd icon_base +2 file, 6/6). Với điều hướng do cha điều khiển (`count` có giá trị) `pos` lấy index/count toàn thư viện (vd 5 của 6) nhưng `ring` chỉ là siblings của asset (1-3 blob) nên `ring[(i-1+n)%n]` = undefined -> `prevBlob` null -> `canCompare` false. Chỉ cổng chọn (không external) chạy được | app/src/renderer/src/components/Lightbox.vue:96-101 |
| 2 | nhẹ | Sau khi nhập từ cổng (dialog Nhập vào thư viện đóng), menu "Gửi tới" bật mở lại (focus trả về nút kích hoạt lại menu), cần Esc | SendToMenu.vue / LibraryImportDialog.vue (chưa khoanh dòng) |
| 3 | quan sát | /api/library/import nhiều path trong 1 lần gom cả thành các version của 1 asset (tên theo tệp đầu), 3 ảnh khác nhau -> 1 asset "icon_base" +2 file; đúng thiết kế (1 run = 1 asset) nhưng dễ gây hiểu nhầm khi API/MCP nhập hàng loạt | core/studio_core/api/library.py:111-117 |
| 4 | quan sát | Ước tính lệch -5,5% trên ảnh nhiễu 2400x1800 (thật lớn hơn); trong ±25% của 18A | core/studio_core/export/audit_flutter.py (estimate_webp) |

CHƯA KIỂM: ảnh cùng kích thước khác (chỉ thử cặp 1024/256); RunDetail so sánh (18C bỏ qua); So sánh >2 ảnh (nút chỉ khi chọn 2).
