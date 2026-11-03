---
date: 2026-11-03
project: AI-Asset-Studio app (renderer)
status: fixed
---

# Cold-start #/new: "Core chưa sẵn sàng" + TypeError 'placeholder', splash kẹt

## Triệu chứng
Reload app thẳng vào `#/new?recipe=...` khi core chưa lên: pageerror `Core chưa sẵn sàng`, `TypeError: Cannot read properties of null (reading 'placeholder')`, splash không tắt.

## Nguyên nhân
`stores/fit.ts` `load()` không phải async, gọi `api()` đồng bộ; `api()` ném khi `transport` còn null. NewRunView gọi `void fit.load()` trong setup nên lỗi làm vỡ setup của route.

## Solution
`load()` trả sớm khi chưa có transport; store tự `watch(conn.transport)` rồi nạp khi core connected. Mẫu tương tự: store có hàm KHÔNG async gọi `api()` đồng bộ ngoài promise (các store khác đã là async nên ném thành rejection, không vỡ setup).

## Context
Tái hiện: kill PID core, `location.hash=...; page.reload()` qua CDP.
