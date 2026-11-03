---
date: 2026-11-03
project: AI-Asset-Studio core + app
status: fixed
---

# Auto-export: thư mục tên "approve", toast trùng, lỗi tiếng Anh thô

## Triệu chứng
Duyệt 1 card nhiều version → 4 toast "Đã xuất" trùng, thư mục đích `approve`; asset không xuất được hiện "nothing to export for this target…" dạng lỗi.

## Nguyên nhân
`AutoExporter._export` gọi `export_engine(name=None)` nên lấy `step.key` của bước gate; event không mang mã lỗi; toast không gộp.

## Solution
Truyền `v.asset_name`; `BundleError.code` (`nothing_to_export`, `bundle_error`) vào event `code`; app (`composables/autoExport.ts`) dịch theo code, `nothing_to_export` là toast info, gộp cùng kết quả/thư mục trong 1,5 s.
