## WP-18QA — PARTIAL
Mục 1 (18A) ĐẠT: 282 MB Flutter, Xem trước 7,8 s, Ghi 12,6 s, ước tính 37,7 vs thật 39,9 MB (-5,5%), nhãn "(ước tính)" hiện, Huỷ giữa Ghi 0,67 s, Hoàn tác md5 khớp.
Mục 2 (18B) ĐẠT: ui-icon-variants -> cổng chọn, phím 1-9, V lightbox, S -> Nhập vào thư viện -> version mới trong Thư viện, bảng phím có V.
Mục 3 (18C) PARTIAL: So sánh trượt/cạnh nhau/nháy, ←→, 1/2/3, Space, 0, zoom+pan đồng bộ, Esc ĐẠT (cặp 1024/256 cùng scale); Lightbox C chạy ở cổng chọn nhưng KHÔNG chạy trong Thư viện.
Mục 4 ĐẠT: renderer.log 0 WARN/ERR/ResizeObserver, 0 pageerror, core.log 0×5xx.
Lỗi: Lightbox.vue:96-101 (prevBlob dùng index/count ngoài lấy ring siblings -> undefined) khiến C/nút So sánh không có trong Thư viện; nhẹ: menu Gửi tới mở lại sau khi dialog Nhập đóng.
Bằng chứng: docs/qa/r18/QA/ (RESULT.md + 01..27 png).
