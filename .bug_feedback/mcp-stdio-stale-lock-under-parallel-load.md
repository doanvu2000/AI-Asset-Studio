---
date: 2026-11-13
project: AI-Asset-Studio core (tests)
status: open (flaky, đã cô lập bằng rerun trong scripts/check.mjs)
---

# test_stdio_spawns_and_stops_core: core.lock.json còn sót sau khi core đã chết

## Triệu chứng
`tests/test_p4b_mcp.py::test_stdio_spawns_and_stops_core` FAIL ở assert cuối `read_lock(data) is None` (lock còn `{'pid': …, 'port': …}`), trong khi `pid_alive(core_pid)` đã False. Chỉ xảy ra khi chạy cả suite `-n 18`; chạy riêng test này pass (2/2). Tần suất khoảng 2/4 lần full suite ngày 2026-11-13.

## Nguyên nhân (chưa chốt)
Core chết mà không chạy tới `finally: remove_lock(...)` trong `studio_core/main.py::_serve` → bị kill cứng chứ không graceful shutdown.
- Đã loại: grace period 2s của MCP SDK (`mcp.client.stdio.PROCESS_TERMINATION_TIMEOUT`). Monkeypatch lên 30s vẫn FAIL.
- Nghi tiếp: `CoreLink.close()` gọi `GET /api/runs` timeout 3s → máy bận thì `httpx` timeout → không gửi `/api/shutdown`; core bị Job Object của proxy giết theo (breakaway không được phép) thay vì `_watch_parent` shutdown graceful.
Production không hỏng: `lockfile.live_core` bỏ qua lock có pid chết.

## Solution (tạm)
`scripts/check.mjs` chạy lại tuần tự các test FAIL (≤ 10); pass thì báo `FLAKY <test>` và không tính FAIL. `--no-rerun` để tắt.

## Context
Tái hiện: `node scripts/check.mjs` (pytest `-m "not slow and not live" -n <threads>`) vài lần khi máy đang tải. Hướng xác minh: log stderr của core spawn (`link.py::_popen_detached` err file) xem có dòng "requested shutdown" / "parent exited" trước khi chết không.
