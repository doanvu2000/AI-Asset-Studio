## WP-14C — DONE
**Đã đổi**: core/studio_core/ops/lut.py (preconditioner V-cycle multigrid: `_restrict`, `_stencil_op`, `_galerkin` (probe 27 lớp màu mod 3), `_dense_from_stencil`, `_smooth` Chebyshev bậc 2, `_mg_precond`; `_solve` chạy `JACOBI_TRIES=6` vòng Jacobi-CG trước rồi mới dựng MG + restart; bỏ `_solve_pyramid`/`COARSE_SMOOTH`; tham số `stats` đếm vòng); core/tests/test_lut.py (+tham số tries=0 buộc đi MG, +test giảm vòng, +test lưới 5/8/9/16/17).
**Tiêu chí 3/3**: (1) số vòng + thời gian fit_lut trước/sau; (2) r8_style trước/sau; (3) test_lut + test_r8_style + test_restyle pass (57 passed).
**Review**: hội tụ cùng CG_TOL=1e-5; ca đơn sắc, 5 mẫu, weight=0 một phần, smooth 0.3/5, lưới chẵn/nhỏ (n=5,8,16) khớp golden gather cũ <1/255/4. Lưới không coarsen được (chẵn >12) -> quay về Jacobi. Coarsest giải bằng pinv (chịu được ô rỗng, mu=1e-6). Chênh LUT vs bản cũ: max 0.026/255 (nhiễu), 0.08/255 (mịn), 0.009/255 (5 mẫu).
**Test** (ảnh tổng hợp như P1: nhiễu 256px 65k mẫu, mịn 192px, 5 mẫu; fit_lut 3 lần, tổng vòng CG 2 pass):
- nhiễu: 30 -> 8 vòng, 0.18 -> 0.20 s (data-dominated, không lợi; hybrid giữ không chậm đáng kể)
- mịn: 139 -> 12 vòng, 0.41 -> 0.30 s
- 5 mẫu: 253 -> 11 vòng, 0.48 -> 0.14 s (~3.4x)
- `pytest tests/test_r8_style.py` tuần tự: 39.8 s (HEAD, đo lại cùng điều kiện) -> 18.0 s (~2.2x; P1 báo 33.6 s lúc máy khác tải).
- test_lut 27 passed; 3 file 57 passed / 33 s.
**Ngoài phạm vi**: CHƯA KIỂM trên ảnh thật qua app (dùng ảnh tổng hợp như P1); core/dist bản sao lut.py không đụng; r8_style chưa đạt 3x vì phần còn lại là setup (_corners, stencil) và việc khác ngoài solver.
**Commit message đề xuất**: perf(lut): multigrid-preconditioned CG for fit_lut (few-sample fits 250 -> 11 iterations, test_r8_style 40 s -> 18 s)
