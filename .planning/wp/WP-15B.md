## WP-15B: Audit fix huỷ — phản hồi nhanh + dung lượng backup đúng
- Từ QA lượt 14 (docs/qa/r14/QA/RESULT.md mục 4, .planning/wp/WP-15QA-report.md):
  (a) Huỷ audit fix → toast trễ ~10 s vì sau khi huỷ request vẫn quét lại project trong cùng request (thanh "Đang chuẩn bị…", nút Huỷ còn bật). Muốn: khi huỷ, trả về ngay (không quét lại, hoặc quét lại chỉ các file đã sửa), response ghi rõ đã huỷ; UI có thể tự quét lại sau nếu cần (ghi "Hot file/UI cần" vào báo cáo, không sửa app/).
  (b) Backup của lần fix bị huỷ ghi "408 MB → 27.0 MB" tính cả phần chưa ghi (core/studio_core/export/audit.py ~946-948 tổng theo `todo` thay vì `written`). Sửa: số liệu theo file thực sự ghi. Áp cả nhánh Flutter (export/audit_flutter.py) nếu cùng lỗi.
- Phạm vi: core/studio_core/export/audit.py, audit_flutter.py, api/audit.py, test core/tests/ (test fail trước, pass sau). Cấm: app/, mcp/server.py.
- Model: sonnet. Không build.
- Nghiệm thu: test huỷ giữa fix → response < 1 s sau huỷ (không gọi scan toàn project), bytes backup = tổng file đã ghi; test audit cũ + flutter pass; full pytest không giảm (1818).
- Nhìn thấy được: không.
- Báo cáo: `.planning/wp/WP-15B-report.md`
## Nhật ký
- audit: apply_fixes (Unity/Godot) gpu_before/after tính theo `todo`; Flutter đã tính theo `done` (đúng); API quét lại sau huỷ nếu written>0.
- fix: audit.py gpu totals theo file đã ghi; api/audit.py huỷ -> trả ngay, rescan_needed. Test mới 2 (test_audit.py): pass. Full pytest 1820 pass, 1 fail test_p4b_mcp stdio (không liên quan).
