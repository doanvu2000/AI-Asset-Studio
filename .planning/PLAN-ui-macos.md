# PLAN — Giao diện kiểu app macOS (2026-10-27)

Chủ nhân: "style giống app macOS, bố trí và các nút đang hơi xấu". Skill: frontend-design + ui-ux-pro-max.

## Chẩn đoán (ảnh trước: scratchpad b257437f…/ui_before/*.png)
- Nút 30–36px, chữ 500–600, nhấn co `scale(.97)` → web. macOS: push button 22–24px, chữ 400, nhấn tối đi.
- Mọi card có bóng + bo 14px → dashboard web. macOS: hộp nhóm phẳng (System Settings), viền 0.5px, không bóng.
- Sidebar dòng 32px chữ 500, 8 mục phẳng. Finder: dòng 28px chữ 400, nhóm có tiêu đề xám nhỏ, icon tô accent.
- Accent tím #6e56cf. macOS: xanh hệ thống.
- `.seg` / ô nhập / select mỗi chỗ một kiểu (`.seg` chép 5 file).

## Token
- Accent: #007AFF (sáng) / #0A84FF (tối). Rủi ro chọn: bỏ tím thương hiệu để đọc ra "macOS" ngay.
- Nền: content #FFFFFF·#1E1E1E; nhóm #F5F5F7·#2A2A2C (+hairline); sidebar vật liệu mờ; chọn rgba(0,0,0,.08)·rgba(255,255,255,.1).
- Chữ: Inter (SF không được đóng gói trên Windows), 13px thân, control 400, tiêu đề 600, tracking âm nhẹ kiểu SF.
- Bo: control 6px, nhóm 10px, sheet 12px. Bóng chỉ cho popover/lightbox/menu.

## Thành phần (base.css, áp toàn app)
- `.btn` 24px, `.btn-lg` 28px, `.btn-icon` 26px vuông, `.btn-plain` = toolbar item không viền. Primary: accent + highlight trên.
- `.field` 24px (textarea tự cao), viền mảnh, focus ring accent 3px kiểu macOS.
- `.seg` dùng chung (24px, thumb sáng có bóng), bỏ bản chép scoped.
- `.card` = hộp nhóm phẳng.
- Sidebar: dòng 28px, nhóm "Làm việc / Kho / Công cụ", badge capsule.
- PageHeader = unified toolbar: tiêu đề 15/600.

## Kiểm
- typecheck; dev:mock + CDP chụp lại dashboard/new/runs/run/gallery/gates/settings, sáng + tối; so trước/sau.
- Không build Portable khi chủ nhân chưa bảo.

## State
- [x] token · [x] base.css · [x] sidebar · [x] header · [x] gỡ .seg trùng (+ .segmented 7 file) · [x] chụp + sửa (ui_after/*.png, sáng + tối) · [x] báo chủ nhân
