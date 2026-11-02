# PLAN 2026-11-03 — Gen voice (chủ nhân mở lại mảng audio, phần giọng nói)

## Hiện trạng
- `core/studio_core/adapters/tts.py` (CPU, sherpa-onnx): Kokoro (EN, chủ nhân thấy "tạm được"), Piper vais1000 (VI, chủ nhân KHÔNG thích).
- Recipe có sẵn: npc-voice-csv, npc-pack, npc-pack-prompt, storyboard-animatic (dùng TTS); manifest pack `tts` (CPU).
- Mẫu giọng VI ở `L:\AI\tts-vi-samples\` (vivos = CC BY-NC-SA → không dùng thương mại; 25hours = chưa rõ license).
- GDD planner đang lọc audio (0374643) — mở lại khi voice sẵn sàng.

## Ứng viên model (nghiên cứu web 2026-11-03, phải kiểm LICENSE file trước khi đưa vào manifest)
| Model | Ngôn ngữ | License | Ghi chú |
|---|---|---|---|
| VieNeu-TTS v3 Turbo (pnnbao97) | VI | Apache 2.0 (repo; kiểm license weights trên HF) | clone 3–8s, chạy CPU realtime, 24/48 kHz — ứng viên số 1 cho VI |
| Chatterbox Multilingual V3 / Turbo (Resemble) | 23+ (kiểm có VI không) | MIT | clone ~5s, chỉnh cảm xúc, tag [laugh]; output có watermark PerTh — ứng viên số 1 cho EN biểu cảm |
| Kokoro 82M | EN (+7) | Apache 2.0 | đã có; dùng cho NPC phụ / narration |
| VoxCPM2 | ~30 (kiểm VI) | Apache 2.0 | dự phòng đa ngữ |
| Qwen3-TTS | zh/en/… (có thể không VI) | Apache 2.0 | dự phòng EN |
| edge-tts (rany2, GPL-3 code) | VI (HoaiMy/NamMinh) + EN | dịch vụ online của Microsoft Edge, ToS không cho dùng ngoài Edge/thương mại; đã bị chặn 1 phần từ 12/2025 | chủ nhân gợi ý: backend TUỲ CHỌN "online — dùng thử/cá nhân", mặc định tắt, cảnh báo license + mạng; thương mại → Azure Speech (cloud, có phí) |
| Kokoro `am_adam` | EN nam trầm | Apache 2.0 (Kokoro-82M) | "giọng Adam" bản local dùng được ngay qua Kokoro ONNX có sẵn |
| ElevenLabs "Adam" | đa ngữ (giọng hay dùng trên TikTok VN) | độc quyền ElevenLabs, chỉ qua API trả phí | KHÔNG clone/sao chép giọng này; chỉ dùng được nếu người dùng tự có key ElevenLabs (backend cloud tuỳ chọn) |
Mix: VieNeu (VI chính) + Kokoro (EN, có am_adam) + Chatterbox (EN biểu cảm) + edge-tts (tuỳ chọn online). Ghi tác giả + license từng model vào AI_CREDITS.md của gói và mục "Cảm ơn" trong app (Settings → Giới thiệu).
Loại: F5-TTS-Vietnamese (CC-BY-NC-SA), viXTTS (CPML, cấm thương mại), Higgs v3 (non-commercial), VibeVoice (research), IndexTTS (cần xin phép), Voxtral (CC BY-NC).

## Giai đoạn
- **V0 Nghe thử (chặn bởi chủ nhân):** cài VieNeu v3 Turbo + Chatterbox vào venv riêng (`tools/tts-*`, không đụng venv core), sinh cùng 6 câu thoại game (VI + EN; giọng nam/nữ/già/trẻ/quái; cảm xúc vui/giận/sợ) → trang nghe so sánh (web-design HTML 1 file có audio nhúng) → chủ nhân chọn.
- **V1 Engine TTS cắm được:** `adapters/tts.py` thành registry backend (sherpa CPU hiện có + backend chạy subprocess trong venv riêng của model GPU/CPU mới), lane theo backend (gpu/cpu), manifest pack `voice_vi`, `voice_expressive` có `hw` (đèn đỏ/vàng/xanh tự hoạt động), cache key gồm model+voice+text+params.
- **V2 Recipe:**
  1. `voice-design` — tạo "giọng nhân vật" từ mô tả (chọn preset gần nhất + chỉnh pitch/speed) hoặc từ file mẫu người dùng (cam kết có quyền dùng giọng đó) → lưu voice profile vào dự án (tái dùng mọi thoại).
  2. `voice-lines` — thoại từ CSV/bảng (id, nhân vật, câu, cảm xúc) → wav/ogg từng dòng, chuẩn hoá loudness (-16 LUFS), cắt im lặng, tên file theo id.
  3. `voice-barks` — biến thể ngắn (đau, tấn công, chết, nhặt đồ…) 3 bản/ý, chọn ở gate.
  4. Cập nhật npc-voice-csv/npc-pack dùng engine mới; mở lại audio trong GDD planner cho voice.
- **V3 Xuất:** Flutter (flame_audio/audioplayers: `assets/audio/voice/<char>/<id>.ogg` + `asset_studio_voice.dart` map id→file + phụ đề json), Android (res/raw + Kotlin map), iOS (bundle + Swift), Unity/Godot như cũ. Phụ đề/timestamp (word-level nếu model hỗ trợ) cho lip-flap đơn giản.
- **V4 UI:** player trong RunDetail/Gate (sóng âm, play từng dòng), voice profile trong ProjectDetail, đèn hợp máy cho recipe voice.

## Nghiệm thu
- V0: chủ nhân chọn được giọng VI + EN.
- V1–V3: pytest backend (mock subprocess), chạy thật 20 câu VI/EN, loudness ±1 LU, dart analyze gói flutter có voice, flutter test phát được asset.

## Ràng buộc
- Không train/fine-tune (không LoRA). Không cloud. Clone giọng chỉ từ file người dùng có quyền; ghi vào AI_CREDITS + cảnh báo trong UI.
- Model mới vào venv riêng, không làm nặng venv core. Ghi license từng model vào manifest (`license`, `commercial`).
