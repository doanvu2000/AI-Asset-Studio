## WP-15QA2 — PARTIAL (Electron thật + core thật, 2026-11-03, cửa sổ 1320x852, sáng, không GPU/ComfyUI)
Cách chạy: STUDIO_DATA_DIR=scratch/qa15q-data, `electron-vite dev -- --remote-debugging-port=9360 --user-data-dir=…` + Playwright connectOverCDP. Seed: dự án "QA Seed Game", 6 ảnh nhập thư viện (library-import), 2 run ui-juice (CPU, cổng duyệt), project Flutter mẫu (3 ảnh 4.7 MB + chưa khai báo + 3.0x thiếu 2.0x) và bản lớn 120 ảnh (560 MB).

### Kết quả từng mục
1. Gửi tới… — ĐẠT, 1 LỖI nhẹ.
   - Thư viện chọn 2 → Restyle (ảnh tham chiếu + dự án điền sẵn, query bị xoá, Back không lặp) / Đóng gói (preselect 2/N). Chọn 1 → thêm 4 recipe gợi ý, Enter → /new điền sẵn ảnh + dự án, Back/Forward không điền lại (chọn nhiều ẩn gợi ý recipe là chủ ý trong mã).
   - RunDetail → Restyle / Đóng gói / Thư viện (lọc project+run đúng). ProjectDetail → Restyle / Kiểm tra project (tự điền thư mục + tự quét) / Đóng gói / Thư viện.
   - Bàn phím: Enter mở, mũi tên/Home/End, Enter chọn, Esc đóng + focus về nút. (02..05, 13..15, 33..38)
   - LỖI nhẹ: `focus=<version>` ("làm nổi bản này") không chọn/cuộn bản nào, không toast, kể cả mở thẳng `#/gallery?project=…&focus=<icon_3>` — GalleryView.vue:462-482 (useQueryPrefill đổi filter/showTechnical rồi watch(items) :329-333 có thể xoá lại selected). (15b..15d)
2. Ctrl+K — ĐẠT, 1 LỖI nhẹ.
   - 4 hành động hiện đủ; "Duyệt cổng đang chờ" mở #/gates?step=… (duyệt 2 cổng được); "Chạy lại lượt gần nhất" khi lượt xong → run mới + mở /runs/<id>; Mở Thư viện/Restyle/Kiểm tra project cho dự án hiện tại. Ctrl+1..8 thật = bảng "?" (Tổng quan, Tạo mới, Cần duyệt, Thư viện, Dự án, Lượt chạy, Công thức, Kiểm tra lỗi). (08..12)
   - LỖI nhẹ: lượt gần nhất đang chờ cổng → 409, toast tiếng Anh thô "Không chạy lại được: run … is still awaiting_human" — CommandPalette.vue:63-71 (lấy runList[0] không lọc trạng thái, không dịch). (09)
3. Đóng gói Android + iOS + 2 shader — ĐẠT với sprite sheet; LỖI với ảnh PNG nhập.
   - Asset sprite (ui-juice, đã duyệt) + Android + iOS + Hit flash + Dissolve → qa-seed-game-1.0.0 (+ zip 411 KB): android/kotlin/AssetStudioShaders.kt (424 dòng), ios/Sources/AssetStudioShaders.metal, ios/Sources/AssetStudioShaderEffects.swift. Lưu ý: AGSL nhúng chuỗi trong .kt (không có file .agsl riêng); Android/iOS nhận cả bộ 6 shader (dialog ghi rõ), không riêng 2 đã chọn. (16, 17)
   - LỖI: chỉ có asset PNG nhập (không phải sprite sheet) + Android/iOS → 500 pack_failed FileNotFoundError '…\.tmp\provenance.json', dialog không báo lỗi dễ hiểu. Asset bị skip cho android/ios nên thư mục work không bao giờ được tạo — core/studio_core/store/pack.py:974 (và 975-977) ghi vào `work` không mkdir. Tái hiện 2/2. (05..07)
   - Quan sát: asset còn "Nháp" không có trong danh sách Pack nhưng preselect rơi về chọn hết (6/6).
4. Audit Flutter — ĐẠT.
   - Project nhỏ: quét 0,03 s → 5 mục đúng (1 chưa khai báo, 1 thiếu 2.0x, 3 ảnh nặng; 14,0 MB) → Xem trước (14,0 → 2,7 MB) → Ghi → tự quét lại "Không có vấn đề nào"; pubspec đổi đúng + giữ comment, .dart đổi đường dẫn, sinh 2.0x/badge.png → Hoàn tác "Đã khôi phục 9 file", quét lại tự chạy ra 5 mục; hash md5 toàn cây khớp trước (30bbc6f1…). (20..24)
   - Huỷ (project 120 ảnh/560 MB): huỷ lúc "Đang chuẩn bị/nén backup" → thanh thoát sau 44 ms, toast, quét lại tự chạy (0,8 s), cây không đổi; huỷ lúc đang ghi (13/122) → "Đang huỷ…" 46 ms → toast "Đã dừng: ghi 13 file, 109 file chưa sửa" sau 0,81 s, thanh mất 0,91 s (<2 s), quét lại tự chạy; Hoàn tác "Đã khôi phục 25 file", hash cây khớp (42dd2392…). (26..32)
   - Quan sát nhẹ: (a) huỷ khi chưa ghi file nào toast ghi "0 file chưa sửa" (đúng ra 122) — AuditView.vue:190 (r.skipped); (b) nhãn "Đã sửa N/122" chạy ~87 s lúc còn nén backup rồi reset "Đã sửa 0/122" khi ghi thật; (c) Xem trước 122 ảnh nặng ~75 s chỉ có spinner.
5. Console — LỖI nhẹ: renderer.log có 18× "ResizeObserver loop completed with undelivered notifications", toàn bộ ở #/gallery (chọn ô → thanh chọn/menu Gửi tới/PackDialog), lẫn với WARN cố ý (500 pack ×2, 409 rerun). Không pageerror, không lỗi ở Audit/Run/NewRun/Gates/Projects/Restyle. (r14: 0× khi đổi cỡ cửa sổ — khác kịch bản.)

### Lỗi tổng hợp
- L1 pack.py:974 — pack chỉ có asset bị skip → 500 FileNotFoundError (trung bình).
- L2 GalleryView.vue:462-482 — focus version không chọn/cuộn (nhẹ).
- L3 CommandPalette.vue:63-71 — chạy lại lượt đang chờ/đang chạy → toast 409 EN thô (nhẹ).
- L4 ResizeObserver 18× ở Gallery (nhẹ, vô hại).
- L5 AuditView.vue:190 — "0 file chưa sửa" khi huỷ sớm (nhẹ).

### Ảnh
docs/qa/r15/QA/*.png (1320x852). Dọn: tắt theo PID (node 38188/34396, cmd 28560, electron 29772 + con 37756/42316/23380, core python 22476), xoá scratch/qa15q-* và thư mục scratch rỗng.
