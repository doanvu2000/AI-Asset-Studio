# PLAN 2026-11-03 — Đề xuất theo cấu hình máy + đèn đỏ/vàng/xanh

Chủ nhân: user có nhiều cấu hình khác nhau → app đề xuất model + chức năng hợp máy; chọn chức năng thì báo
đỏ (không đủ, không nên chạy) / vàng (chạy được nhưng chậm/offload/thiếu thứ cài được) / xanh (tốt).

Hiện có: `bootstrap/plan.py` detect_gpu (nvidia-smi name/driver/VRAM total), free_bytes; `bootstrap/manifest.json`
packs + models (size, không có VRAM cấu trúc); lane theo executor (contracts.Lane); Electron `device.ts` đọc CPU/RAM/GPU;
`GET /api/setup`. Recipe không khai yêu cầu phần cứng.

## Contract (cố định, 2 WP song song theo contract này)
`GET /api/hardware` →
```json
{"gpu": {"name": "RTX 5070", "vram_total_mb": 12227, "vram_free_mb": 9000, "driver": "580.1", "ok": true} | null,
 "ram_total_mb": 32000, "ram_free_mb": 12000, "cpu_threads": 16, "disk_free_mb": 400000,
 "tier": "low|mid|high"}
```
`GET /api/fit` →
```json
{"hardware": {...như trên...},
 "recipes": {"<recipe_id>": {"level": "green|yellow|red", "reasons": ["…tiếng Việt…"], "needs": {"vram_gb": 12, "ram_gb": 32, "lanes": ["gpu"], "packs": ["base"]}, "missing_packs": ["edit"]}},
 "flows": {"<flow_id>": {cùng shape, gộp mức tệ nhất các node}},
 "recommend": {"packs": [{"id": "edit", "label": "…", "level": "green|yellow|red", "reason": "…", "installed": false, "size_mb": 20500}],
               "lane_limits": {"cpu": 4}, "notes": ["…"]}}
```
Luật mức:
- red: cần lane gpu mà không có GPU NVIDIA / driver dưới min / VRAM < min_vram_gb và RAM không đủ offload (< min_ram_gb); thiếu RAM tối thiểu; disk không đủ cho pack cần cài.
- yellow: VRAM < rec_vram_gb (chạy offload, chậm); pack chưa cài (cài được); tool chưa cấu hình (Blender/Claude chưa login/ComfyUI chưa có).
- green: còn lại.
Yêu cầu phần cứng khai trong `manifest.json` mỗi pack: `"hw": {"min_vram_gb", "rec_vram_gb", "min_ram_gb"}` (giá trị suy từ size model + ghi chú `detail` có sẵn); recipe = gộp các pack recipe dùng (`recipe_packs`) + lane executor; blender lane → cần Blender; cloud → cần Claude.

## HW-1 core (sonnet) — `core/studio_core/hardware.py` (mới), `api/hardware.py` (mới), `bootstrap/manifest.json` (thêm hw), `bootstrap/plan.py` (đọc), routes đăng ký, tests. Cache probe 30s. RAM Windows qua ctypes GlobalMemoryStatusEx (không thêm dependency), fallback /proc/meminfo.
## HW-2 app (sonnet) — types + client + mock, store `fit`, component `FitBadge.vue` (chấm màu + tooltip lý do), gắn vào GoalCards, NewRunView chọn recipe, RecipesView, flow picker/FlowBatch; xác nhận trước khi chạy khi red; mục "Phù hợp với máy" trong Settings (setup pane): cấu hình + pack đề xuất + danh sách chức năng xanh.
## Nghiệm thu: pytest -k "hardware or fit or setup or manifest"; npm run typecheck; Playwright mock chụp badge + màn Settings.
