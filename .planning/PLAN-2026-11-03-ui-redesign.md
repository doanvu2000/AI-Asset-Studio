# PLAN 2026-11-03 — UI đẹp, clean, dễ dùng (chủ nhân: áp web-design + stageshow + rule CLAUDE.md)

Nguồn style: `~/.claude/skills/web-design/SKILL.md` (Apple light: token, font Be Vietnam Pro + JetBrains Mono,
cỡ chữ lớn, card bo 18 không viền không bóng, hairline, pill luôn kèm chữ, không emoji/gradient, không cuộn ngang,
min-width:0 / minmax(0,1fr)); `~/.claude/skills/stageshow/SKILL.md` (minh hoạ UI sản phẩm cho onboarding/empty state);
memory `ui-motion-speed-hig.md` (motion mượt, số animate, trạng thái bước rõ, nhanh).
App là Electron/Vue (không phải trang 1 file) → lấy token/typography/component rules, không lấy template HTML.
Light là mặc định; dark giữ, dựng lại cùng hệ (Apple dark) vì app đã có dark.

## UI-1 Design system (nền) — opus (quyết định kiến trúc style toàn app)
- Token :root light/dark theo web-design; font nhúng local (copy woff2 từ skill assets/fonts vào app, @font-face, offline).
- Base: body 15–16px app density (web-design 17px cho trang đọc; app dùng 15/16 nhưng nhãn ≥12px), H1/H2 scale, tabular-nums.
- Component chung: button (primary/secondary/ghost/danger), field, card, pill (c1..c6 + trạng thái), segmented, PageHeader, sidebar, empty state, toast; FitBadge dùng pill.
- Không phá logic; chỉ CSS + component trình bày. Nghiệm thu: typecheck, build renderer, Playwright mock chụp 6 màn chính light/dark 1280/1024, đo scrollWidth = viewport.
## UI-2 Màn hình (sau UI-1, 3 agent song song theo nhóm view rời nhau)
- A: Dashboard, GettingStarted, GoalCards, NewRun, RunDetail, NextStepChips.
- B: Runs/History, Gallery/Library, Projects, ProjectDetail, PackDialog, BundleMenu, Export.
- C: Settings (mọi pane, FitPanel, SetupPanel), Recipes, Flows/FlowEditor/FlowStart/FlowBatch, Gdd, GpuPanel.
- Mỗi màn: 1 việc chính rõ, giảm chữ, nhóm UPPERCASE nhãn nhỏ, khoảng thở, không trang trí thừa.
## UI-3 Minh hoạ stageshow — onboarding (GettingStarted) + empty states: still PNG/WebP dựng bằng stageshow, nhúng vào app.
## Nghiệm thu chung: main xem ảnh từng màn; Electron thật 1 lượt cuối; commit từng gói.
