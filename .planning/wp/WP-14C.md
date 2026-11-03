## WP-14C: Multigrid preconditioner cho lut._solve
- Mục tiêu: WP-12P1 (f192457) đưa fit_lut nhanh 3.5–4x nhưng CG vẫn ~200 vòng trên lưới 33³; test_r8_style 33.6 s. Thêm preconditioner (V-cycle multigrid hoặc Jacobi/Chebyshev hợp lý trên stencil 27 ô sẵn có) để số vòng CG giảm mạnh; giữ numpy thuần (không scipy). Kết quả khớp golden test_lut (≤1/255, hiện max 0.05/255).
- Phạm vi: core/studio_core/ops/lut.py, core/tests/test_lut.py. | Cấm mọi file khác.
- Model: sonnet.
- Tiêu chí: số vòng CG trước/sau + thời gian fit_lut (2 ảnh thật như P1) + test_r8_style trước/sau (chạy tuần tự, số thật); test_lut + test_r8_style + test_restyle pass.
- Báo cáo: `.planning/wp/WP-14C-report.md`
## Nhật ký
- [AUDIT] đọc lut.py/test_lut.py: CG Jacobi-precond 1 điểm; kế hoạch: V-cycle Galerkin (probe 27 màu) làm precond, Jacobi/Chebyshev smoother, coarsest dense solve.
- [IMPL] V-cycle Galerkin+Chebyshev precond, hybrid (6 Jacobi CG trước, rồi MG); bench few5 253->11 vòng, 0.46->0.14s
- [TEST/REPORT] 57 passed; r8 39.8->18.0s; báo cáo WP-14C-report.md xong
