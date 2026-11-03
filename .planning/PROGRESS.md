# PROGRESS — AI Asset Studio

Chỉ giữ phiên đang chạy (≤ ~150 dòng). Phiên cũ đã đóng → `progress/PROGRESS-<ngày>.md`.
Resume: đọc HANDOFF mới nhất trước; chỉ mở file lưu trữ khi cần tra lịch sử (grep theo mã WP/commit).

## Lưu trữ
| File | Nội dung |
|---|---|
| progress/PROGRESS-2026-10-early.md | trạng thái phase P1–P3, việc còn mở ban đầu |
| progress/PROGRESS-2026-10-27.md | phiên 27/10 |
| progress/PROGRESS-2026-10-28.md | phiên 28/10, R6–R11, G1–G2, F1–F6 (Spine, 3D, VFX, LoRA spike) |
| progress/PROGRESS-2026-10-29.md | phiên 29/10, lượt 5–6 |
| progress/PROGRESS-2026-10-30.md | phiên 30/10, lượt 7 |
| progress/PROGRESS-2026-10-31.md | phiên 31/10 |
| progress/PROGRESS-2026-11-01.md | lượt 8, live r8, lượt 9 (workflow) |
| progress/PROGRESS-2026-11-02.md | lượt 10–11, hw-fit, Flutter exporter/shader/Flame demo, UI redesign, release 1.4.0 |

## Phiên hiện tại
## Phiên 2026-11-03 — lượt 12 (manager-work-agent)
Chủ nhân: "làm liên tục; tối ưu quy trình, UI/UX, hiệu năng + 3 chức năng mới". Commit từng phần + push (theo lượt 11, cả phiên), được build/chạy app để test, không trailer. Plan: PLAN-2026-11-03-r12.md.
- [x] R12-RES-features: top 3 G parity Compose/SwiftUI, D normal AI, F theme (wp/R12-RES-features.md). [~] R12-RES-audit (sonnet).
- [~] WP-12F theme (sonnet) + WP-12D normal AI (sonnet) giao, chạy nền.
- [x] R12-RES-audit: 7 gói A–G (wp/R12-RES-audit.md): A test-speed, B chạy lại+seed, C versions phân trang, D batch/preset, E export tự động, F Gallery/editor UX, G Audit/Restyle states + bundle.
- [x] WP-12F theme — export/theme*.py, store/pack.py, api/pack.py+style_bible.py; 41 pass (main chạy), dart analyze 0 + flutter test, compileDebugKotlin OK, iOS chưa biên dịch; error tách khỏi primary đỏ (main xem swatch). main sửa: types.ts PackBody.theme. Chủ nhân chốt commit từng phần + push cuối — c10ed32.
- [x] WP-12C versions phân trang (offset+X-Total-Count), index project_status, Gallery QA chunk 500 — 30 pass (main chạy), typecheck sạch; main sửa: client.ts limit/offset, mock slice, bỏ import thừa — bf2c73a. CHƯA NHÌN Gallery >200.
- [x] WP-12G1 Compose 7 kind (android_fx*.py) — 31 pass (main chạy), compileDebugKotlin OK, hồi quy byte-identical; CHƯA CHẠY THIẾT BỊ — 3fcbb70.
- [~] WP-12A test-speed (sonnet) giao. Đang chạy: 12D, 12G1, 12A.
- [~] WP-12G2 SwiftUI parity (sonnet) giao. Đang chạy: 12D, 12A, 12G2.
- [x] WP-12D normal AI MoGe-2 ViT-S (MIT, venv riêng L:\AI\setup\normal-ai, setup/normal-ai.ps1), method auto|ai|filter, fallback filter — 20 pass (main chạy); main xem compare.png: AI khối mượt, filter vân chi tiết; cầu lỗi góc 32°→4.5°; lạnh 4.2–5.3 s/ảnh (load 2.3 s), sheet ~2 s/khung — 1fa8db5. Nợ: manifest pack normal-ai chưa thêm (cấu trúc packs gắn downloader model), nút cài trong Settings.
- [~] WP-12U1 PackDialog theme + Audit/Restyle empty + PageHeader fit + skeleton + bundle motion-v (sonnet) giao. Đang chạy: 12A, 12G2, 12U1.
- [x] WP-12A pytest-xdist + marker slow/live, BUILD.md mục test: tuần tự 442 s → `-n auto --dist loadfile` 91 s; `-m "not slow"` 60 s — 4d51c8c. Chưa đạt: thu nhỏ ảnh r8_style không nhanh hơn (nút thắt ops/lut.py::_solve 4.3 s/5 lần) → nợ WP tối ưu _solve.
- [x] main: gen_agent_docs sau đổi recipe sprite-normal-map (test_skill_reference_is_current hết fail).
- [~] WP-12P1 tối ưu lut._solve (sonnet) giao. Đang chạy: 12G2, 12U1, 12P1.
- [x] WP-12G2 SwiftUI 7 kind (ios_fx*.py) — 33 pass (main chạy), hồi quy byte-identical 59 file; CHƯA BIÊN DỊCH (không Xcode; FXI_OUT dump để Mac `swiftc -typecheck`) — d11b331. main sửa android_fx KDoc tên màn có `*/`/xuống dòng — commit riêng.
- [~] WP-12E tự xuất khi duyệt (sonnet) giao. Đang chạy: 12U1, 12P1, 12E. Tiếp: B chạy lại+seed (sau U1, cùng RunDetailView), W3/W4/W5 NewRun preset/batch seed/auto-pick.
- [x] WP-12E tự xuất khi duyệt (engine/auto_export.py nghe version.updated, khoá theo thư mục, event project.auto_export, toast) — 16 pass (main chạy), full not-slow 1688 pass (agent); main xem ProjectDetail light; main sửa: types.ts ProjectRow/PatchBody/EventType, events.ts bỏ cast, mock project fields — ea5b588. CHƯA NHÌN toast. Nợ: MCP update_project 2 tham số.
- [x] WP-12U1 PackDialog theme, Audit/Restyle EmptyState, PageHeader fit, NewRun skeleton + nhãn method, bỏ motion-v (index 695→327 kB) — typecheck sạch; main xem pack_theme_on_dark; main gỡ dep motion-v — commit sau ea5b588. CHƯA quay chuyển động pill sidebar.
- [x] WP-12P1 lut._solve stencil 27 ô + coarse→fine — fit_lut 3.5–4.2x, lệch ≤0.05/255; test_r8_style 45.6→33.6 s (1.5x, chưa 3x: CG ~200 vòng, cần multigrid) — 32 pass (main chạy). main xoá core/p.out (cProfile rác).
- [~] WP-12B chạy lại + seed (sonnet) + WP-12FX Gallery lọc 1 hàng / Flow Delete+Undo / Ctrl+Enter (sonnet) giao.
- [x] WP-12B seed_used mỗi step + POST /runs/{id}/rerun {seed new|same} + nút Chạy lại/giữ seed + seed trong inspector — 53 pass (main chạy), typecheck sạch; main xem rerun_menu_dark; main sửa: types StepMeta.seed_used, client rerunRun, bỏ cast rerun.ts. Cache mặc định không đổi. Nợ: MCP rerun_run, menu Chạy lại ở RunsView, step map không pin seed.
- [~] WP-12M MCP (update_project auto_export, rerun_run, pack theme, get_ui_theme) + WP-12N NewRun preset/batch seeds/auto-pick giao (sonnet). Đang chạy: 12FX, 12M, 12N.
- [x] WP-12M MCP rerun_run, get_ui_theme, build_asset_pack theme, update_project auto_export — 13 pass (main chạy), docs không drift. main sửa: rerun giữ gate_policy+source (test) — commit riêng.
- [x] WP-12FX Gallery lọc 1 hàng + popover, Flow Delete/Ctrl+Z/Y (≤50), Ctrl+Enter FlowStart/Audit/Restyle — typecheck sạch (main chạy), main xem gallery_popover_light. CHƯA KIỂM: confirm xoá node có dây, Ctrl+Enter thực. Delete chưa xoá dây.
- [~] WP-12NI pack cài normal-ai từ app (sonnet) giao. Đang chạy: 12N, 12NI. Sau: QA Electron thật (WP-12QA), README/W7, full pytest, push, handoff.
- [x] WP-12N preset người dùng (run_presets + /api/presets/runs), batch 1 prompt × N seed ($meta.seed_override), auto-pick QA cho gate.pick — 95 pass (main chạy cùng NI), typecheck sạch; main xem vary_seeds_dark; main sửa: client.ts 3 method preset, runPresets.ts bỏ fetch riêng — 0aad2e2. Nợ: lỗi có sẵn cold-start #/new fit.load, recipes reload reset field.
- [x] WP-12NI pack normal-ai cài từ app (uv venv CPU, MoGe ghim b942f00, weights), nhận thư mục cũ L:\AI\setup\normal-ai — cài thật 47 s vào đường dẫn có khoảng trắng — 202149e; main thêm license MoGe vào model_licenses. Chủ nhân xoá tay: `L:\AI\setup\_ni test` (~1 GB). CHƯA KIỂM nút cài trên UI.
- [~] WP-12QA Electron thật (sonnet) + full pytest -n auto (main, nền).
- [x] Full pytest -n auto --dist loadfile: 1747 pass, 6 skip, 80 s (sau f83db77).
- [x] WP-12QA Electron thật: 1–3,5–7 ĐẠT, 4 ĐẠT phần (auto-pick thật cần GPU), lỗi L1 cold-start #/new (cao), L2 reset field, L3 toast trùng + tên thư mục "approve", L4 lỗi tiếng Anh thô, Q1 preset không lưu chế độ seeds — docs/qa/r12/QA/RESULT.md — 7ce0367.
- [~] WP-12FIX (L1–L4, Q1, Q2) + WP-12R README/changelog (sonnet) giao.
- [x] WP-12R README 87 dòng + changelog 1.5.0-dev (chưa phát hành, không bump) — typecheck sạch, test_version 2 pass.
- [x] WP-12FIX L1 fit.load cold-start, L2 giữ field khi recipe reload, L3 thư mục theo asset_name + gộp toast, L4 BundleError.code dịch VN (nothing_to_export = info), Q1 preset lưu seed_count/seed_base; Q2 giữ nguyên (gate approve ≠ duyệt asset) — 9 pass + typecheck (main chạy), ảnh docs/qa/r12/FIX — a9f6746.
- [x] Full pytest song song: 1749 pass, 6 skip, 74 s. Handoff: HANDOFF-latest.md.

## Phiên 2026-11-03 — lượt 13
Chủ nhân: "cứ làm liên tục". Plan: PLAN-2026-11-03-r13.md. Commit từng phần + push cuối.
- [~] WP-13A RunsView chạy lại + Delete dây + bàn phím StepNode; WP-13B normal AI worker giữ ấm; WP-13C tiến độ quét Audit/Restyle/QA — 3 agent sonnet chạy nền.
- [x] WP-13B normal AI worker ấm: lạnh 4.2 s → ấm 1.7–2.0 s/ảnh 256 px; review opus 0 HIGH/MED, 5 LOW đã sửa (cờ closed, dup fd1, dòng lạ, loop khác, REAP_SECONDS) — 40 pass (main chạy).
- [x] WP-13C scan.progress + huỷ cho Audit/Restyle/QA recompute, ScanProgressBar — 30 pass + typecheck (main chạy); main xem audit_scanning_light; main sửa: types EventType scan.progress + QaRecomputeResult.cancelled, client cancelScan, bỏ fetch riêng/cast — 43262b9.
- [x] WP-13A menu Chạy lại ở RunRowItem, chọn dây + Delete/Esc + thanh Gỡ dây, Enter/mũi tên giữa node — typecheck sạch — e2bf0ee. CHƯA KIỂM Electron thật.
- [x] Full pytest sau 13A–C: 1768 pass, 6 skip, 78 s. [~] WP-13QA Electron thật (sonnet).
- [x] WP-13QA Electron thật: 1,2,4,5 ĐẠT; 3 ĐẠT (Audit 4000 tệp huỷ ở 601, Restyle huỷ, Gallery huỷ giữ điểm đã chấm); normal AI 4.0/1.7/1.6 s, worker tự tắt 120 s, đóng app không sót PID — docs/qa/r13/QA. Lỗi: nhãn "ảnh cũ" không giảm (QA_VERSION renderer 1 ≠ core 2) → main sửa + test đồng bộ — 2f04cd0; thanh Audit dừng ~318/409 khi sang analyze → main sửa scan_jobs + test — commit sau.
- [x] Full pytest 1770 pass. Handoff cập nhật lượt 13. Push.
