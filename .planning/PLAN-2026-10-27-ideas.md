# PLAN — 4 ý tưởng còn treo (2026-10-27 sáng)

Chủ nhân chốt "làm cả 3": commit+push sửa mở (xong `6d19023`), làm 4 ý tưởng NIGHT §3 mục 8–10, rồi build + chép Portable.
Quyền phiên này: commit + push, build, chép Portable, chạy GPU/API để kiểm.

## Luồng (agent song song, KHÔNG chạy git ghi; con commit)

| Luồng | Phạm vi | File chính (dự kiến) |
|-------|---------|----------------------|
| W17 | Bóng đổ + outline sprite (`op.sprite_fx`), bước tuỳ chọn trong recipe sprite; QA điểm chất lượng (heuristic, lưu theo version, badge + chi tiết + sắp xếp/lọc trong Thư viện) | `core/studio_core/ops/fx.py`, `ops/qa.py`, recipe sprite-8dir-*, `app` Thư viện |
| W18 | Recipe tileset/texture liền mạch: Z-Image → `op.seamless` → `op.tile_check` (điểm seam) → preview 3×3; viewer lát gạch trong app | `ops/tile.py`, `recipes/texture-seamless.yaml`, app viewer |
| W19 | Bảng chi phí/thời gian theo recipe: API tổng hợp runs (số lượt, thời gian TB/p50/p90, chi phí, tỉ lệ lỗi, theo ngày) + thẻ/bảng trên Tổng quan | `core/studio_core/api/stats.py`, app Tổng quan |

File dùng chung (chỉ thêm dòng, không format lại): `engine/kinds.py`, `ops/__init__.py`, router API, `app/src/api/*`.

## Kiểm

- `cd core && uv run pytest -q`; `cd app && npm run typecheck`.
- Live: 1 lượt tileset thật, 1 lượt sprite có fx (cache được), ảnh QA app (electron dev hoặc bản đóng gói).
- Audit sau khi gộp → sửa → commit/push → build → `update-portable.ps1`.

## State
- [x] Sửa mở (outline hex, palette cancel, cape) → `6d19023` pushed.
- [x] W17 · [x] W18 · [x] W19 · [x] audit · [x] build + Portable · [x] handoff
