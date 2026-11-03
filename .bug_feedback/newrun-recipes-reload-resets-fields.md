---
date: 2026-11-03
project: AI-Asset-Studio app (renderer)
status: fixed
---

# NewRunView: recipes reload ~100-200 ms sau mount xoá chữ vừa gõ

## Triệu chứng
Gõ vào `#in-prompt` ngay sau khi vào `#/new?recipe=...`, giá trị về '' sau <1,5 s (lúc có lúc không).

## Nguyên nhân
`watch(selected)` chạy lại mỗi khi `recipes.load()` trả object mới cùng id và xoá `values`/`touched`.

## Solution
Nhớ `initialisedFor` (id đã khởi tạo); cùng id thì bỏ qua, về picker thì reset. Tái hiện tất định: gõ rồi `pinia._s.get('recipes').load(true)`.
