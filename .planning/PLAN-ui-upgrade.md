# PLAN — Nâng cấp UI theo UI-AUDIT (2026-10-18 tối)

Nguồn: `.planning/UI-AUDIT.md` (28 phát hiện). Skill bắt buộc: `frontend-design` + `ui-ux-pro-max`. Art mới: Codex
(`app/scripts/gen-art.py` + `art-manifest.json`). Test: `npm run typecheck` (app), `uv run pytest` (core), chụp màn
`dev:mock` qua CDP. Không build installer.

Chia 4 agent song song, file không chồng nhau:

| Agent | Mục audit | File sở hữu |
|---|---|---|
| A graph | #2, #22, confirm bug, chữ 10px node | `components/flow/**`, `views/RecipeEditorView.vue` |
| B run+gate | #3, #5, #6, #7, #15, #16, #17 (thanh segment), #28, thumbnail list gate | `views/RunDetailView.vue`, `components/StepChip.vue`, `views/GatesView.vue`, `components/gate/**` (mới), `composables/errorAdvice.ts` (mới) |
| C export+shell | #1, #19, #20, #23, #24, #17 (taskbar/Notification) | `src/main/**`, `src/preload/**`, `components/AssetActions.vue` (mới), `Lightbox.vue`, `GalleryView.vue`, `CoreOffline.vue`, `CoreBanner.vue`, `App.vue`, `SettingsView.vue`, `ToolPathsPanel.vue`, `stores/versions.ts`, core export endpoint |
| D vocab+lists | #4 (run rows), #8, #9, #10, #11, #12, #13, #14, #18, #21, #25, #26, #27, tokens, art Codex | `composables/format.ts`, `styles/**`, `RunRowItem.vue`, `AppSidebar.vue`, `stores/runs.ts`, `stores/batches.ts`, `DashboardView.vue`, `ResourceCard.vue`, `NewRunView.vue`, `BatchView.vue`, `BatchDetailView.vue`, `RunsView.vue`, `RecipesView.vue`, `EmptyState.vue`, `CommandPalette.vue` (mới), `assets/art/**`, `SpineViewer.vue`, core run preview_hash, mock server |

Hợp đồng chung: `AssetActions.vue` props `{ hash: string; filename?: string; runId?: string; versionId?: string }`
(C làm, B dùng). Mock server (`api/mock/server.ts`) chỉ D sửa; agent khác cần gì thì ghi chú cho lead.

Sau 4 agent: lead typecheck + pytest + chụp màn mock, sửa xung đột, commit theo nhóm, cập nhật PROGRESS.
