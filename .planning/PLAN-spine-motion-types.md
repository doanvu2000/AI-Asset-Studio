# PLAN — spine-anim-ref cho mọi kiểu chuyển động (không chỉ sway)

Hiện tại: motion spec = 1 tín hiệu lắc hông + thân theo + arm_keys tự do. Hợp idle/sway/vẫy tay; không diễn tả được
đi/chạy/nhảy/đánh/ngồi (cần chân, trọng tâm dịch chuyển lớn, root motion, contact chân).

## Hướng
1. Spec mở rộng giữ tương thích: thêm `keys = {bone: {rotate|translate|scale: [[t, v...]]}}` tổng quát (mọi bone, mọi
   kênh), `ik_targets = {target_bone: [[t, x, y]]}` để chân/tay IK chạm đất, `root_motion` (dịch toàn thân), `loop`
   (anim lặp: đầu = cuối thay cho đầu/cuối = setup).
2. Sway trở thành một "preset" sinh `keys` (code hiện có), các preset khác: walk/run cycle (pha chân, bob hông 2 lần
   mỗi chu kỳ, tay ngược pha), jump (anticipation-air-land), attack (wind-up/strike/recover).
3. Ràng buộc chung giữ nguyên: giới hạn khớp (seam/proven), không key bone generated/constraint-driven, graph mượt.
4. Review: kiểm tra trượt chân (foot contact trượt > x px khi đứng đất), xuyên đất, loop seam (frame cuối ≈ đầu).
5. Nghiệm thu: spineboy (so với walk/run sẵn có làm chuẩn), mix-and-match, tach.

## Câu hỏi cho chủ nhân
- Ưu tiên preset nào trước (walk/run, jump, attack…)?
- Anim lặp (loop) hay one-shot về setup như sway?

## Đã làm (2026-10-28, mặc định tự chọn vì câu hỏi chưa có trả lời: walk/run trước; cycle = loop, one-shot = về setup)
- Spec: `loop`, `keys` (rotate/translate/scale mọi bone, kẹp joint limit, thắng mọi thứ sinh tự động), `cycle`
  (walk/run, side/front, direction, stride/lift/bob/hip_sway theo chiều dài chân, contact, arm_swing, forearm, lean).
  Schema chỉ bắt buộc `duration`, `ease`. root_motion không làm riêng: cycle chạy tại chỗ, one-shot dùng `keys` root.
- `ops/spine_cycle.py`: chân = IK target leo lên tổ tiên cuối không phải root/không trên hông (spineboy
  front-leg-target → front-foot-target); chân trụ trượt lùi đều (key tuyến tính 24/chu kỳ), chân bước cung eased;
  sải canh dưới gốc đùi, hông tự hạ để chân với tới (≤1.03L, hạ tối đa 0.2L rồi mới thu sải); hông nhún 2 lần/chu
  kỳ; tay ghép chân theo token tên (front/rear, L/R) rồi theo x, vung ngược pha, cẳng tay trễ + kẹp limit; thân
  nghiêng theo hướng đi. Mặc định đo từ walk/run spineboy-pro + mix-and-match-pro.
- Kiểm tra: `foot_contact` (trượt = lệch khỏi đường thẳng đều khi chân chạm đất, lún dưới setup; lỗi >6% / >5% chân;
  walk/run gốc của 2 rig mẫu đều qua), `check_animation(loop=)` frame cuối = frame đầu. Review: ghi chú Foot check,
  cycle không tính chồng gối là lỗi, bỏ key trên bone do constraint điều khiển (báo lại).
- Prompt recipe: chọn loại chuyển động (sway / walk-run cycle / one-shot keys).
- 2026-10-28: thêm preset `jump` (one-shot) — xem PROGRESS G2.
- Còn lại: preset attack riêng (hiện Claude tự key bằng `keys`), lăn bàn chân (heel/toe roll), UI modal hiện
  foot check, nghiệm thu live với video walk/run thật.
