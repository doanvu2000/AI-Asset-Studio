Bản 1.4.0 chọn quy trình theo cấu hình máy, xuất Flutter / Flame đầy đủ kèm demo chạy được, và giao diện thiết kế lại.

## Tải về

| File | Dùng khi |
|------|----------|
| `AI-Asset-Studio-Portable-1.4.0-x64.zip` | Giải nén ra đâu cũng chạy, dữ liệu nằm trong thư mục đó (khuyên dùng) |
| `AI-Asset-Studio-Setup-1.4.0-x64.exe` | Cài vào máy, có shortcut Start menu / Desktop |

Cần Windows 10/11 x64, GPU NVIDIA ≥ 12 GB VRAM (driver ≥ 580), khoảng 140 GB ổ trống nếu lấy đủ model. Đang dùng bản Portable cũ: giải nén đè lên thư mục cũ, thư mục `data\` được giữ nguyên.

## Mới trong 1.4.0

**Quy trình**
- Đèn hợp máy đỏ / vàng / xanh khi chọn mục tiêu, quy trình, flow; app dò GPU, RAM, ổ đĩa và đề xuất model theo máy.
- Plan asset từ GDD bằng Claude, xác nhận và sửa trong bảng; kế hoạch được lưu lại để xem lịch sử.
- Thêm quy trình: bóng + viền (shadow-outline), ổn định chân về một điểm xoay chung, chân dung cảm xúc, sửa vùng ảnh.
- Hoạt ảnh một lần quay về tư thế đứng; hoạt ảnh vừa khung theo chiều cao thân, không tính vũ khí.
- Pixel art tự chọn số màu; ground-strip giữ đế giày chạm cỏ; bóng đổ nằm đúng chân.
- Chân dung cảm xúc khớp tỉ lệ với bản trung tính.

**Xuất engine**
- **Flutter / Flame**: tile động, autotile kèm bản đồ `.tmx` (flame_tiled), VFX hạt, thời tiết, màn UI, thẻ bài, minimap, icon launcher.
- **Shader Pack Flutter** 6 hiệu ứng + sprite ánh sáng theo normal map.
- **Demo Flame chạy được** (`flutter run`): nhân vật 8 hướng, bàn phím / joystick, camera theo, map, parallax, hitbox; có ô chọn trong hộp đóng gói.
- **Android** (WebP theo mật độ, `.9.png`, icon launcher, map Kotlin) và **iOS** (xcassets, SwiftUI / SpriteKit).
- Gói Android: tài nguyên có tiền tố `asset_` khi tên bắt đầu bằng số.
- Đóng gói chỉ lấy bản mới nhất mỗi asset (trừ khi chọn nhiều bản).
- Gói có file khai báo AI (AI_CREDITS) cho store.

**App**
- Giao diện thiết kế lại: font Be Vietnam Pro + JetBrains Mono, sáng / tối, bố cục gọn hơn ở Tổng quan, Lượt chạy, Dự án, Thư viện, Cài đặt.
- Minh hoạ ở thẻ Bắt đầu và các màn trống.
- Chế độ Đơn giản / Nâng cao.
- Xem log từng bước trực tiếp.

**Sửa lỗi**
- Thanh công cụ xem sprite không xuống dòng; gợi ý canvas trống không che hình.
- Ô tổ chức gói Unity chỉ hiện khi xuất Unity.
- GDD: bỏ recipe âm thanh khỏi kế hoạch, không còn quá thời gian chờ Claude.
- Lỗi ở trình chỉnh flow khi mở lần đầu.
