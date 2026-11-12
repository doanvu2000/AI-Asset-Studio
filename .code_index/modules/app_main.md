# app_main — Electron main + preload

Gốc `app/src/main/`, `app/src/preload/`. Config: `app/electron.vite.config.ts`, `app/electron-builder.yml`, `app/package.json`.

## Main process
- `app/src/main/index.ts` — tạo `BrowserWindow`, `CoreSupervisor`, `Devlog`, `AssetExporter`; đăng ký IPC `studio:*` qua `handle(...)`; dev = `ELECTRON_RENDERER_URL`, mock = `VITE_MOCK=1`; portable khi có file `portable` cạnh exe; `RENDERER_ORIGIN` kiểm origin IPC; `RateLimiter` giới hạn log từ renderer.
- `app/src/main/core.ts::CoreSupervisor` — spawn `studio-core serve`, đọc dòng `STUDIO_CORE_READY`, hoặc attach core đang chạy qua `core.lock.json` (`parseCoreLock`, `pidAlive`); restart có backoff (`BACKOFF_MAX_MS` 30s, ổn định sau 60s), `EXIT_ALREADY_RUNNING` 3 → attach; emit `studio:coreStatus`; `killTree` khi thoát; `dataDir`, `secrets`.
- `app/src/main/devlog.ts::Devlog` · `RotatingLog` · `RateLimiter` · `redact` · `listLogs` · `readTail` — log xoay vòng ở `<data>/logs`, che token/secret.
- `app/src/main/diagnostics.ts::buildReport` · `buildBundle` · `parsePayload` — báo cáo/zip chẩn đoán (Cài đặt → Nhật ký chẩn đoán), cắt `REPORT_MAX_CHARS`.
- `app/src/main/agentInstall.ts::installAgent` · `agentStatus` · `manualCommands` · `codexBlock` · `upsertCodexSection` — cài plugin Claude Code / Codex (marketplace `MARKETPLACE`, plugin `PLUGIN`), sao lưu `pruneBackups`.
- `app/src/main/assets.ts::AssetExporter` — export asset ra đĩa/zip cho renderer (`prepare`, `saveAs`, `reveal`, `saveBundle`); `checkStep` / `checkHash` / `checkPreset` / `checkEngineOptions` / `checkName` kiểm đầu vào IPC (hash 64 hex, step `[\w-]{1,80}`).
- `app/src/main/zip.ts::buildZip` — zip thuần Node. `device.ts::deviceInfo` — GPU/RAM. `enginePlugin.ts::installEnginePlugin` · `detectEngine` · `enableGodotPlugin` — cài bridge Unity/Godot vào project game.

## Preload
- `app/src/preload/index.ts` — `contextBridge.exposeInMainWorld('studio', bridge)`: `getConnection` (baseUrl + token), `onCoreStatus`, `log`, `diagInfo`, `restartCore`, `pickPath`, `revealPath`, `agentInstall`, `installEnginePlugin`, `asset*`, `setProgress`, `setDirty`, `notify`.
- `app/src/preload/types.ts::StudioBridge` · `CoreStatus` — kiểu dùng chung; `global.d.ts` khai `window.studio`.

## Luồng kết nối
`CoreSupervisor` ready → `studio:coreStatus{state:'ready', baseUrl}` → renderer `connection.ts` gọi `getConnection()` → `createRealTransport(baseUrl, token)` (xem `app_ui.md`).

## Invariant
- Mọi tham số IPC từ renderer phải qua hàm `check*` trước khi chạm FS/core.
- Thoát app khi còn thay đổi chưa lưu thì hỏi `confirmDiscard` (renderer báo qua `studio:dirty`).
- Dev không cần core: `npm run dev:mock` (mock transport trong `renderer/src/api/mock/`).
