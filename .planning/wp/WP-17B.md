## WP-17B: Cổng duyệt "Gửi tới…"
- Mục tiêu: ở GatesView / GatePicker / GateApprove (và Lightbox khi xem ứng viên), mỗi ứng viên có "Gửi tới…" (SendToMenu, tái dùng sendTo.ts đích: NewRun recipe gợi ý, Restyle, Nhập thư viện, Chạy flow). Ứng viên bị loại vẫn gửi được (blob còn trong kho). Phím tắt: phím `S` mở menu cho ứng viên đang focus (kiểm gate/gateUtil.ts guards, không đụng phím chọn/duyệt có sẵn); ghi vào bảng phím tắt CommandPalette.vue (chỉ thêm 1 dòng bảng).
- Phạm vi: app views/GatesView.vue, components/gate/*.vue, gate/gateUtil.ts, components/Lightbox.vue (nếu cần), CommandPalette.vue (chỉ dòng bảng phím). Cấm: sendTo.ts logic đích (nếu cần đích mới ghi báo cáo), hot file, core.
- Model: sonnet. Không build. typecheck. Ảnh docs/qa/r17/WP-17B/ (mock hoặc Electron thật).
- Báo cáo: `.planning/wp/WP-17B-report.md`
## Nhật ký
- 14:30 audit+code xong (SendToMenu expose show, gateUtil candidateSource/isSendKey, Picker/Approve footer + S, Lightbox sendFor+S, palette dòng S); chụp 01 approve
- xong, report ghi
