## WP-17A — DONE
Đã đổi: RestyleView/NewRunView prefill lỗi -> toast VN qua sentFileError; core api/pack.py listing thêm `engines` (giữ engine_ready) + assert trong test_pack.py; types.ts PackCandidate.engines; PackDialog nhãn assetNote ("chỉ Android/iOS"); mock server.ts: catalog 6 shader id thật + packCandidates.engines (engine_ready=false cho ảnh); mock flows.ts: thêm flow built-in `sprite-lighting` (input ảnh).
Test: pytest test_pack.py 12 passed; npm run typecheck sạch.
Nhìn: docs/qa/r17/WP-17A/01_restyle_toast.jpg, 02_pack_dialog.jpg.
