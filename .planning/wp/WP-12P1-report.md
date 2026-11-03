## WP-12P1 — DONE
**Đã đổi**: core/studio_core/ops/lut.py (`_solve` → stencil A^T W A + Laplacian gộp, flat-padded shift, nhánh sparse; `_solve_pyramid` warm start coarse→fine + `_prolong`; pass robust warm start; `CG_TOL=1e-5`, bỏ `_laplacian`); core/tests/test_lut.py (+5 test, golden = solver gather cũ nhúng trong test, không file nhị phân).
**Tiêu chí 3/3**: (1) benchmark 5 lần; (2) khớp bản cũ, test pass; (3) test_r8_style trước/sau.
**Audit**: nút thắt là x[i] gather + bincount mỗi vòng CG (400 vòng, 2 pass). Không có scipy trong venv nên dùng stencil thuần numpy. Không thêm lru cache (không thấy gọi lặp cùng input; chưa kiểm thực tế trong app).
**Review**: ảnh đơn sắc (mono), 5 mẫu, 6000 mẫu, weight=0 một phần, smooth 0.3/5 đều khớp golden < 1/255/4. Mask rỗng do fit_pair xử lý (StepError) trước khi tới _solve, không đổi. Đổi `CG_TOL` 1e-6→1e-5 đã đo: lệch ≤0.07/255.
**Test**
- fit_lut 5 lần (ảnh 256px nhiễu, 65k mẫu): 0.72-0.87 s → 0.21-0.25 s (~3.5x). Ảnh mịn 192px: 2.09-2.39 s → 0.49-0.59 s (~4.2x).
- Khớp LUT cũ vs mới (HEAD): max |Δ| 0.044/255 (mịn), 0.034/255 (nhiễu); mean ≤ 0.006/255. Đạt ≤ 1/255.
- `pytest tests/test_r8_style.py` (tuần tự): 45.6 s → 33.6 s; tổng fit_lut trong đó (51 lần, chỉ ~5 mẫu duy nhất): 39.3 s → 26.9 s (~1.5x). Ca ít mẫu bị chặn bởi ~200 vòng CG trên 36k ô nên chưa đạt 3x; máy đang tải bởi agent khác nên số có nhiễu.
- `pytest tests/test_lut.py tests/test_r8_style.py tests/test_restyle.py tests/test_style_bible.py tests/test_w9_project_style.py -n auto --dist loadfile` → 53 passed, 34 s.
**Ngoài phạm vi**: CHƯA KIỂM: ca ít mẫu >=3x (cần multigrid preconditioner); lru cache. core/dist/... bản sao lut.py không đụng.
**Commit message đề xuất**: perf(lut): stencil-based CG solve with coarse-to-fine warm start for fit_lut (~3.5-4x on real images, results within 0.05/255)
