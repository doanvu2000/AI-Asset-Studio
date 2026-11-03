## WP-14B: Tiến độ + huỷ cho audit/fix và project_qa
- Mục tiêu: tái dùng core/studio_core/scan_jobs.py (kind mới 'audit_fix', 'project_qa') + components/ScanProgressBar.vue + composables/scanProgress.ts: audit fix (sửa .meta/.import hàng loạt) và project QA (MCP project_qa + nút trong app — tìm nơi gọi) hiện N/M + Huỷ. Huỷ audit fix phải an toàn: dừng giữa 2 tệp, backup zip đã ghi vẫn hoàn tác được phần đã sửa (giữ cơ chế undo hiện có); báo số tệp đã sửa.
- Phạm vi: core export/audit.py (fix), api/audit.py (fix endpoint), nơi project_qa (tìm), scan_jobs.py (chỉ mở rộng kind nếu cần), tests/test_scan_progress.py hoặc test mới; app AuditView.vue (phần fix), view/component chạy project QA, composables/scanProgress.ts (ScanKind), api/types.ts + client.ts (ĐƯỢC sửa: kind mới trong cancelScan). | Cấm: FlowCanvas, RunPresetMenu, runPresets, run_presets.py (WP-14A), ops/lut.py (WP-14C).
- Skill: web-design. Model: sonnet.
- Tiêu chí: pytest: progress đúng thứ tự, huỷ giữa fix → undo khôi phục đúng phần đã sửa, project_qa huỷ OK; typecheck sạch; ảnh mock fix đang chạy + project QA đang chạy (light+dark) → docs/qa/r14/WP-14B/.
- Báo cáo: `.planning/wp/WP-14B-report.md`
## Nhật ký
- core: apply_fixes progress/huỷ + audit_fix job + project_qa job + 4 test mới (pass)
- app: ScanKind/cancelScan kinds, ScanProgressBar verb, AuditView fix bar+toast huỷ, QaPanel bar, mock; typecheck sạch
- ảnh fix_running/fix_cancelled light+dark ok (mock)
- test 54 pass, typecheck sạch, ảnh qa_running/qa_cancelled light+dark; dọn vite/config tạm
