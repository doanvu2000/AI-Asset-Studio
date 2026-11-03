## WP-18B — DONE
- Đã đổi: core api/library.py (hashes: regex + tồn tại trong blob store, 400 invalid_hash / 404, gộp với paths), mcp/server.py (import_to_library thêm hashes, paths tuỳ chọn), ops/hue_variants.py + kinds.py + ops/__init__.py (op.hue_variants), recipes/ui-icon-variants.yaml (CPU: xoay hue N bản -> gate.pick), gen_agent_docs (2 recipes.md); app types.ts (hashes), mock/server.ts, sendTo.ts (importBlobs), gate/gateUtil.ts (candidateSource đặt importBlobs), SendToMenu.vue, LibraryImportDialog.vue (hiện tên ứng viên), CommandPalette.vue (dòng "V — Xem lớn (cổng chọn ảnh)").
- Tiêu chí: ứng viên cổng gửi được "Nhập vào thư viện" không cần đường dẫn; path traversal / hash hoa / hash lạ bị từ chối, không tạo version.
- Test: pytest toàn core (xdist loadfile) xanh, gồm tests/test_wp18b.py mới (hashes, hash xấu, gộp paths+hashes, hue alpha, lint recipe, chạy recipe tới gate.pick rồi resolve) + test_agent_mcp (MCP hashes); npm run typecheck sạch.
- Nhìn: docs/qa/r18/WP-18B/01_import_dialog_from_blob.jpg, 02_import_toast.jpg (mock cổng 5318, đã tắt theo PID).
- Lưu ý: app mock chỉ có cổng approve (hero.json) để thử; QA vẫn cần recipe ui-icon-variants cho cổng pick thật (thay qa17-pick). File cấu hình vite tạm đã xoá.
- Commit message: feat(library): import blobs by hash from gate candidates (API, MCP, Send-to), V hint, and CPU ui-icon-variants recipe with pick gate
