## WP-13B: Normal AI worker giữ ấm
- Mục tiêu: hiện mỗi lần op gọi spawn subprocess mới → nạp model 2.3 s + infer ~2 s. Giữ 1 worker sống (stdin/stdout JSON lines hoặc cơ chế có sẵn trong adapters khác), tái dùng giữa các step/run, tự tắt sau N giây rảnh (vd 120 s, hằng số có tên), tắt khi core shutdown (lifespan), khởi lại nếu chết/timeout. Huỷ step giữa chừng → kill worker (như hiện kill cây tiến trình), lần sau spawn lại. Đồng thời: một hàng đợi/lock cho worker (CPU, tránh 2 infer chồng).
- Phạm vi: core/studio_core/adapters/normal_ai.py, adapters/normal_ai_worker.py, core/studio_core/app.py (chỉ hook shutdown nếu cần), core/tests/test_normal_ai.py. | Cấm: ops/normal2d.py trừ khi bắt buộc (ghi lý do), api/**, app/**, bootstrap/** .
- Model: sonnet.
- Tiêu chí: đo thật trên máy (L:\AI\setup\normal-ai đã cài): ảnh đầu (lạnh) vs ảnh 2..5 (ấm) s/ảnh, mục tiêu ấm ≤ 2.5 s cho 256 px; test mock: tái dùng worker, tự tắt khi rảnh (thời gian giả), worker chết → spawn lại, huỷ → kill; full test normal pass; không để tiến trình mồ côi sau test (kiểm PID).
- Báo cáo: `.planning/wp/WP-13B-report.md`
## Nhật ký
- worker: thêm chế độ --serve (JSON lines, model cache), giữ one-shot
- normal_ai.py: _Pool/_Worker giữ ấm (lock, idle 120s, retry 1 lần khi worker cũ chết, kill khi huỷ/timeout), shutdown(), worker_pids()
- app.py: _shutdown gọi normal_ai.shutdown()
- tests: +9 test với fake worker thật (tái dùng, tuần tự, idle, chết→respawn, huỷ, timeout)
- đo thật: lạnh 4.59s, ấm 1.60-1.74s/ảnh 256px; shutdown không mồ côi
- báo cáo đã ghi
