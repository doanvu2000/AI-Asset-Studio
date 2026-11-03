# PROGRESS — AI Asset Studio

Chỉ giữ phiên đang chạy (≤ ~150 dòng). Phiên cũ đã đóng → `progress/PROGRESS-<ngày>.md`.
Resume: đọc HANDOFF mới nhất trước; chỉ mở file lưu trữ khi cần tra lịch sử (grep theo mã WP/commit).

## Lưu trữ
| File | Nội dung |
|---|---|
| progress/PROGRESS-2026-10-early.md | trạng thái phase P1–P3, việc còn mở ban đầu |
| progress/PROGRESS-2026-10-27.md | phiên 27/10 |
| progress/PROGRESS-2026-10-28.md | phiên 28/10, R6–R11, G1–G2, F1–F6 (Spine, 3D, VFX, LoRA spike) |
| progress/PROGRESS-2026-10-29.md | phiên 29/10, lượt 5–6 |
| progress/PROGRESS-2026-10-30.md | phiên 30/10, lượt 7 |
| progress/PROGRESS-2026-10-31.md | phiên 31/10 |
| progress/PROGRESS-2026-11-01.md | lượt 8, live r8, lượt 9 (workflow) |
| progress/PROGRESS-2026-11-03.md | lượt 12–18: theme, normal AI, Compose/SwiftUI fx, rerun/seed/preset, tiến độ+huỷ quét, Flutter lint, AGSL/Metal, Gửi tới…, nhập thư viện, xem trước shader, so sánh 2 bản (QA docs/qa/r12–r18) |
| progress/PROGRESS-2026-11-02.md | lượt 10–11, hw-fit, Flutter exporter/shader/Flame demo, UI redesign, release 1.4.0 |

## Phiên hiện tại
## Phiên 2026-11-03 16:1x — lượt 19 (sau HANDOFF-1605)
Chủ nhân: làm liên tục, 3 tính năng mới, tối ưu UI-UX + flow. Commit từng phần + push cuối.
- [x] PROGRESS lượt 12–18 cắt sang progress/PROGRESS-2026-11-03.md. Plan: PLAN-2026-11-03-r19.md.
- [~] WP-19A/B/C sonnet.
- [x] WP-19B Ctrl+K Gửi gần đây (sendto:recent ≤8, try/catch, gỡ khi tệp hết) — typecheck sạch (main chạy), ảnh mock (main sửa: nguồn fallback tên dự án) — 4d20352.
- [x] WP-19A nhập mỗi tệp 1 asset (group per_file|single, items, MCP group, dialog radio + tên khi gộp) — 31 pass + typecheck (main chạy) — 81f5f7d.
- [x] WP-19C so sánh 2 lượt chạy (RunDiff: kết quả, thời gian, đầu vào, params, seed; chỉ dòng khác; mở CompareView) từ Lịch sử + RunDetail — typecheck sạch, ảnh mock (main xem 04) — 84b08fb.
- [~] WP-19QA Electron thật (sonnet).
- [x] WP-19B commit 4d20352 (ghi ở trên). WP-19QA Electron thật 1–5 ĐẠT (Lightbox C Thư viện, Gửi tới không mở lại, nhập per_file/gộp/trùng tên, Gửi gần đây nhớ qua khởi động lại + gỡ mục hỏng, RunDiff + seed khác, console sạch) — docs/qa/r19/QA — 682f8e3. Main sửa nhẹ: focus về nút Gửi tới sau dialog, chạy lại mục gần đây đưa lên đầu — 6aaf059. Nợ: RunDiff input ảnh tạo qua API hiện hash trần (RunDiff.vue:99-101).
- [x] Full pytest lượt 19: 1885 pass (1 flake chạy lại pass), 6 skip. Push.
- [x] 16:50 Handoff mới: .planning/HANDOFF-2026-11-03-1650.md (1605 → archive).
