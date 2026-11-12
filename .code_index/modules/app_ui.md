# app_ui — Vue renderer

Gốc `app/src/renderer/src/`. Vue 3 + Pinia + vue-router (`createWebHashHistory`), font Be Vietnam Pro, token CSS `styles/tokens.css`.

## Khởi động + kết nối
- `main.ts` — `createApp(App).use(createPinia()).use(router)`; `App.vue` = shell (sidebar, `CoreBanner`, `ToastHost`, `CommandPalette`).
- `router.ts::router` — route: `/` dashboard, `/new`, `/runs`, `/runs/:id`, `/gates`, `/gallery`, `/recipes(/:id)`, `/flows` (+ `/flows/new`, `/:id/edit`, `/:id/run`, `/runs/:id`, `/batches/:id`), `/gdd`, `/batch`, `/projects(/:id)`, `/settings`, `/restyle`, `/audit`.
- `stores/connection.ts::useConnection` — nhận `CoreStatus` từ preload, tạo transport; `api()` (throw nếu chưa sẵn sàng), `blobUrl`, `thumbUrl`, `flushEvents`.
- `api/transport.ts::createRealTransport` · `createMockTransport` (`MOCK` flag) → `{api, stream}`.
- `api/client.ts::StudioApi` (interface mọi endpoint) · `createHttpApi` · `ApiError` — fetch + Bearer token; `api/types.ts` = kiểu khớp `contracts.py`; `auditTypes.ts`, `restyleTypes.ts`, `hitFx.ts`, `runPresets.ts`, `enginePreview.ts` kiểu theo vùng.
- `api/ws.ts::StudioSocket` · `FrameBatcher` — WS `/ws?t=`, gom event theo frame (`flush`), reconnect + `resume{after_seq}`.
- `api/mock/server.ts` + `catalog.ts` / `flows.ts` / `stats.ts` — server giả cho `dev:mock`.

## Store (Pinia, `stores/`)
- `events.ts::applyEvents` · `resyncAll` — áp event WS vào các store; resync khi mất event.
- `runs.ts::useRuns` (run/step/log live) · `gates.ts::useGates` (+`gateFromStep`) · `versions.ts::useVersions` · `projects.ts::useProjects` · `flows.ts::useFlows` · `batches.ts::useBatches` · `catalog.ts::useKinds` / `usePresets` · `recipes.ts::useRecipes` · `resources.ts::useResources` · `gpu.ts::useGpu` · `fit.ts::useFit` · `agents.ts::useAgents` · `toasts.ts::useToasts`.

## Màn chính (`views/`)
- `NewRunView.vue` (form recipe → `start_run`, preset, batch, prefill), `RunDetailView.vue` (step, log, output, export, follow-up), `RunsView.vue`, `GatesView.vue` (duyệt pick/approve), `GalleryView.vue` (version, lọc, so sánh), `ProjectDetailView.vue` (style bible, manifest, QA, pack), `RecipeEditorView.vue` + `components/flow/*` (`FlowCanvas.vue`, `RecipeInspector.vue`, `StepInspector.vue`), `FlowEditorView.vue` / `FlowRunView.vue` / `FlowStartView.vue` / `FlowBatchView.vue`, `GddPlanView.vue`, `RestyleView.vue`, `AuditView.vue`, `SettingsView.vue`, `DashboardView.vue`.

## Component dùng chung (`components/`)
- Viewer asset: `components/viewers/AssetViewer.vue` chọn theo `viewers/detect.ts` → `SpriteSheetViewer` · `GlbViewer` (three, `glbScene.ts`) · `SpineViewer` · `PanoViewer` · `TileViewer` · `AudioViewer` · `VideoViewer` · `TextViewer`.
- Export / gửi: `BundleMenu.vue`, `PackDialog.vue`, `AtlasDialog.vue`, `HitFxDialog.vue`, `ShaderPicker.vue`, `SendToMenu.vue`, `ExportToEngine.vue`, `EnginePreview.vue`, `OpenInEngine.vue`, `AutoExportRow.vue`.
- Hệ thống: `SetupPanel.vue` (cài model), `GpuPanel.vue`, `FitPanel.vue`, `ResourceCard.vue`, `ToolPathsPanel.vue`, `DiagnosticsPanel.vue`, `AgentConnectCard.vue`, `AgentActivity.vue`.
- QA/style: `QaPanel.vue`, `StyleBiblePanel.vue`, `StyleLockPanel.vue`, `SimilarDrawer.vue`, `DuplicatesPanel.vue`, `CompareView.vue`, `RunDiff.vue`.

## Composables (`composables/`)
- `graph.ts` / `flowGraph.ts` / `flows.ts` — dựng + bố cục graph recipe/flow (cạnh, kiểm kiểu cổng); `undoStack.ts` (undo/redo, `UNDO_LIMIT` 50, flow editor); `review.ts` (logic duyệt gate); `sendTo.ts::sendItems` (gửi output sang recipe khác); `followUp.ts`; `format.ts` (định dạng số/thời gian/giá); `eta.ts`; `errorAdvice.ts` (lỗi → gợi ý); `shellIntegration.ts`; `projectStyle.ts`; `changelog.ts` (nội dung "Có gì mới").

## Invariant
- Mọi gọi core qua `api()` (có token); ảnh dùng `blobUrl`/`thumbUrl` (token query).
- State đồng bộ bằng event WS + `resyncAll`; không poll từng run.
- Typecheck: `cd app && npm run typecheck` (`vue-tsc` web + `tsc` node). Không có test frontend trong repo.
