## WP-12C: /api/versions phân trang + index (audit H2, H6, H3)
- Mục tiêu: dự án >200 version không còn thiếu âm thầm. Core: `GET /api/versions` hỗ trợ `offset` (hoặc cursor `before`) + trả tổng (header `X-Total-Count` hoặc field — giữ tương thích client cũ: mặc định response shape cũ không đổi). Store app `stores/versions.ts` tải hết theo trang (hoặc trang kế khi cuộn) — Gallery/Gates/đếm đúng tổng. Thêm migration index `idx_versions_project_status(project_id,status,created_at DESC)`. Recompute QA ở GalleryView (limit 5000 một phát) → chunk 500 có tiến độ nếu chạm được mà không sửa nhiều GalleryView (nếu cần sửa GalleryView >20 dòng thì bỏ, ghi báo cáo — GalleryView để gói UX sau).
- Phạm vi: core/studio_core/store/repo.py, store/db.py (migration), api/routes.py (chỉ endpoint versions/runs list), app/src/renderer/src/stores/versions.ts, tests mới core/tests/test_versions_paging.py. | Cấm (hot): app/src/renderer/src/api/types.ts + api client (ghi patch vào báo cáo nếu cần đổi chữ ký), mcp/server.py, manifest.json, store/pack.py, api/pack.py, api/style_bible.py, export/*, ops/normal2d.py, adapters/normal_ai*.
- Song song với: WP-12D, WP-12F | Model: sonnet
- Tiêu chí:
  - [ ] Test: DB 450 version → tải đủ 450 qua phân trang; tổng đúng; lọc project/status đúng; client không gửi offset → hành vi cũ.
  - [ ] Migration chạy trên DB cũ idempotent; EXPLAIN QUERY PLAN dùng index mới (ghi vào báo cáo).
  - [ ] `cd app && npm run typecheck` sạch.
  - [ ] pytest versions/routes/repo liên quan pass.
- Nhìn thấy được: không (dữ liệu) — nếu chạy mock UI được thì chụp Gallery với >200 version.
- Báo cáo: `.planning/wp/WP-12C-report.md`
## Nhật ký
- repo.list_versions offset + count_versions, tiebreak id; route limit/offset + X-Total-Count
- db migration idx_versions_project_status; EXPLAIN dung index moi
- stores/versions.ts fetchAll phan trang 200; GalleryView recompute chunk 500
- test_versions_paging.py 7 test pass; pytest lien quan 88 pass; typecheck sach
