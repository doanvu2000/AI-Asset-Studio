## WP-12A — PARTIAL (3/4 tiêu chí; r8_style không rút gọn được)
Đã đổi: core/pyproject.toml (+pytest-xdist dev dep, markers slow/live), core/uv.lock, `@pytest.mark.slow` cho 16 test >=4 s (r8_style x5, r7_tile_anim x3, r7_spine_face x3, skybox, lut, anim_video, agent_mcp, r8_decal_crack), BUILD.md mục "Chạy test core". Không đặt addopts.
Tiêu chí 1/4: song song `-n auto --dist loadfile` = 3 fail/1675 pass/6 skip, 90.7 s; tuần tự = 3 fail/1654 pass/6 skip, 441.8 s (7m22). Chênh số test do test_normal_ai.py mới xuất hiện giữa 2 lần chạy (agent WP-12D).
Tiêu chí 2/4: không test nào fail riêng khi song song, không cần serial/xdist_group.
Tiêu chí 3/4: `-m "not slow" -n auto --dist loadfile` = 59.8 s (<=90) — 1660 pass, 2 fail, 6 skip.
Tiêu chí 4/4 CHƯA ĐẠT: thử ảnh 64 px (size//4, viền //4) -> r8_style gần như không nhanh hơn (35 s/15 test) và 2 test đổi kết quả (restyle không flag). Nguyên nhân thời gian là studio_core/ops/lut.py `_solve` (4.3 s/5 lần gọi), độc lập cỡ ảnh -> đã hoàn lại, giữ ảnh gốc, chỉ đánh slow. Đề xuất: tối ưu _solve hoặc cache LUT (ngoài phạm vi).
Audit: diff chỉ decorator/marker/dep; không đụng code studio_core, app/, file cấm.
Review: marker dùng cùng conftest sẵn có (live) nên khai báo thêm trong pyproject không xung đột.
Test: fail ghi nhận, không sửa: test_agent_mcp::test_skill_reference_is_current[claude-plugin|codex] (có từ lần tuần tự đầu, không liên quan WP); tuần tự: test_normal2d::test_op_with_grid... (WP-12D, sau đó đã pass); song song: test_normal_ai::test_cache_key_depends_on_method_and_model (WP-12D đang viết).
Ngoài phạm vi: `_solve` trong ops/lut.py chậm; skill_reference fail cần chủ nhân/agent khác xem.
Commit message đề xuất: test: add pytest-xdist and slow marker, document quick parallel test run (60 s vs 7 min sequential)
