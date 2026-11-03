## WP-12FIX — DONE
Đã đổi: stores/fit.ts, views/NewRunView.vue, composables/autoExport.ts, stores/events.ts, components/RunPresetMenu.vue (1 dòng hint), core engine/auto_export.py, store/bundle.py (BundleError.code), tests/test_auto_export.py (+2), .bug_feedback/ (3 file).
Từng lỗi:
- L1: fit.load trả sớm khi chưa có transport + store tự nạp khi connected. Fail trước: HEAD fit.ts, kill core + reload #/new → pageerror "Core chưa sẵn sàng"; sau: errs [] (B02_before_after.png vs 02_cold_after.png). Store khác cùng mẫu: không có (còn lại đều async, lỗi thành rejection).
- L2: NewRunView bỏ qua reset khi recipe cùng id nạp lại. Tất định: gõ rồi recipes.load(true): trước ""; sau giữ nguyên; 12/12 trial giữ (03_new_typed_kept.png).
- L3: auto_export truyền v.asset_name; toast gộp cùng project/kết quả/thư mục trong 1,5 s. Thật: card weather 16 version → 1 toast, thư mục theo tên asset. pytest test_folder_named_after_asset_not_step_key fail khi bỏ fix, pass sau.
- L4: BundleError.code (nothing_to_export, bundle_error) → event.code; app dịch VN; nothing_to_export = toast info (09_info_toast_nothing_to_export.png). pytest test_nothing_to_export_carries_code.
- Q1: preset lưu seed_count/seed_base khi batch-seeds; áp lại chuyển sang Nhiều prompt + seeds (N=3, gốc 100) (04/05_preset_*.png).
- Q2: không đổi. Gate approve = "cho pipeline chạy tiếp" (có thể ở bước trung gian/auto), version approved = duyệt asset ở thư viện; nghe gate sẽ xuất cả bản chưa chốt và bỏ qua nút Duyệt.
Test: pytest test_auto_export+test_export_engines+test_step_export+test_engine_preview → 44 passed; npm run typecheck → sạch.
Nhìn: docs/qa/r12/FIX/ 01-05, 09, B01-B02 (đã mở xem; Electron thật + core thật, CDP).
Hot file cần thêm: api/types.ts — thêm `code?: string` vào payload event project.auto_export nếu muốn bỏ kiểu cục bộ; không bắt buộc.
Ngoài phạm vi: toast gộp chỉ ở renderer (backend vẫn phát 1 event/version); tên thư mục = tên card (dài, bị cắt bởi safe_component).
Commit message đề xuất: fix(app,core): cold-start fit load, keep typed fields on recipe reload, seed presets, auto-export asset folder name, merged toasts and translated errors
