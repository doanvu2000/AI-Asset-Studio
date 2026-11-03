## WP-12A: Tăng tốc test suite (audit H9)
- Mục tiêu: vòng test nhanh cho dev + full suite nhanh hơn. (a) pytest-xdist vào core venv (`core/.venv/Scripts/python.exe -m pip install pytest-xdist`, thêm vào file dependency dev của core nếu có), `--dist loadfile`; (b) marker `slow` đăng ký trong core/pyproject.toml, đánh dấu test >5 s (đo bằng --durations), KHÔNG đặt addopts loại slow mặc định (giữ full suite như cũ), thêm hướng dẫn chạy nhanh `-m "not slow" -n auto` vào BUILD.md mục test; (c) test_r8_style dùng ảnh nhỏ (64 px) nếu không đổi ý nghĩa test.
- Phạm vi: core/pyproject.toml (chỉ mục pytest/dev deps), core/tests/conftest.py, core/tests/test_r8_style.py, test file khác chỉ thêm decorator `@pytest.mark.slow`, BUILD.md. | Cấm: mọi file code trong core/studio_core, app/, test_theme_*, test_versions_paging*, test_normal*, test_export_android_fx* (agent khác đang viết).
- Model: sonnet.
- Tiêu chí:
  - [ ] Full suite `-n auto --dist loadfile` pass cùng số test như chạy tuần tự (ghi số pass/skip/fail và thời gian cả 2 cách; test fail do file agent khác đang sửa thì ghi rõ, không sửa).
  - [ ] Test nào fail khi chạy song song (chung port/file/global) → sửa fixture cho cô lập hoặc đánh `@pytest.mark.serial`/xdist_group, ghi lý do.
  - [ ] `-m "not slow" -n auto` ≤ 90 s (ghi số thật).
  - [ ] test_r8_style thời gian trước/sau.
- Báo cáo: `.planning/wp/WP-12A-report.md`
## Nhật ký
- xdist 3.8.0 cài bằng uv add --dev (pyproject+uv.lock)
- seq baseline: 3 failed (2 agent_mcp skill_reference, 1 normal2d), 1654 pass, 6 skip, 441.8s
- đánh slow 16 test >=4s trong 8 file + marker pyproject; shrink r8_style KHÔNG hiệu quả (thời gian ở ops/lut.py _solve, độc lập cỡ ảnh) nên giữ nguyên ảnh
- BUILD.md thêm mục "Chạy test core"; not-slow -n auto: 2 fail(agent_mcp skill_reference, có sẵn), 1660 pass, 6 skip, 59.8s
