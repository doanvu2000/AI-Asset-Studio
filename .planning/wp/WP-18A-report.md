## WP-18A — DONE
Đã đổi: core/studio_core/export/audit_flutter.py (estimate_webp lấy mẫu 48 tile 192px full-res cho ảnh >2 MP, bias 0.92, ảnh nhỏ encode chính xác; plan_fixes song song ≤min(6,cpu-1) có cửa sổ trước, huỷ giữa đơn vị; Ghi encode song song + ghi file tuần tự; bytes_after thật sau khi ghi; estimated trên unit/doc; ước tính sát ngưỡng "không nhẹ hơn" (≥0.8) thì encode lại chính xác; backup zip STORED cho png/jpg/webp). App: AuditView "(ước tính)" + auditTypes.ts `estimated?`.
Số đo (project tự sinh 230 MB, 59 ảnh WebP, 8 mosaic 5016x3762): Xem trước 49.5 s -> 6.9-7.4 s; Ghi 57.8 s -> 12.3 s (zip STORED 19.1 -> 12.7). Ước tính 24.1 MB vs thật 23.8 MB (+1..+8%).
Độ chính xác: lưới screenshot mosaic/ảnh thật/giả photo 0.81-1.16 sau bias; test ±25%.
Test: test_audit_flutter_estimate.py 13 pass (±25% x7, nhỏ=chính xác, dry estimated vs write thật, song song == tuần tự, huỷ khi plan, huỷ giữa đơn vị + undo, recheck sát ngưỡng). Full pytest 1880 pass/1 fail test_p4b_mcp::test_stdio_spawns_and_stops_core (flaky khi tải; pass chạy lẻ); vue-tsc sạch.
Hot file: không cần thêm (auditTypes.ts không phải types.ts hot).
Commit: perf(audit): sampled WebP size estimate in Flutter dry run, parallel plan/encode with sequential writes, stored backup zip for compressed images; ui: mark estimated sizes
