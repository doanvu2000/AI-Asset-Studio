# Build bản cài Windows

## Cần có

- Windows 10/11 x64
- Node 24 + npm (đã `npm install` trong `app/`)
- uv ≥ 0.11 (tự lấy Python 3.13 cho core)
- Khoảng 2 GB trống cho `core/build`, `core/dist`, `app/release`

## Build một lệnh

```powershell
cd app
npm run dist
```

Các bước bên trong:

| Script | Làm gì |
|---|---|
| `npm run dist:core` | `uv --directory ../core run pyinstaller packaging/studio-core.spec --noconfirm --clean` → `core/dist/studio-core/` (onedir) |
| `npm run dist:app` | kiểm tra đã có `studio-core.exe` → `electron-vite build` → `electron-builder --win --x64` |
| `npm run dist` | chạy hai bước trên nối tiếp |

Sửa riêng UI thì chạy lại `npm run dist:app` (giữ nguyên core đã build).

## Output

- Core: `core/dist/studio-core/studio-core.exe` + `_internal/`
- Bản cài: `app/release/<version>/AI-Asset-Studio-Setup-<version>-x64.exe`
- Bản chạy thẳng không cần cài: `app/release/<version>/win-unpacked/`

Trong bản cài, core nằm ở `<thư mục cài>\resources\core\studio-core.exe`. App tự spawn `studio-core.exe serve`; agent CLI (Claude Code / Codex) nối qua `studio-core.exe mcp`. Lệnh kết nối đúng đường dẫn hiện ở **Tổng quan → Kết nối agent CLI**.

## Dữ liệu và đường dẫn công cụ

- Dữ liệu người dùng: `%APPDATA%\AI Asset Studio\data` (DB, blob, run, preset, recipe tự tạo). Gỡ cài không xoá thư mục này. Đặt `STUDIO_DATA_DIR` nếu muốn để chỗ khác.
- Đường dẫn công cụ ngoài lưu trong `data\settings.json`. Sửa trong app: **Cài đặt → Đường dẫn công cụ** (có kiểm tra tồn tại + version), hoặc sửa tay file JSON:

```json
{
  "comfy_dir": "L:\\AI\\setup\\comfyUI",
  "comfy_python": "L:\\AI\\setup\\comfyUI\\.venv\\Scripts\\python.exe",
  "comfy_models_dir": "F:\\work\\AI\\comfyUI_models",
  "comfy_workflows_dir": "L:\\AI\\setup\\workflows",
  "blender_exe": "C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe",
  "codex_exe": "C:\\...\\codex.exe"
}
```

ComfyUI / Blender đang chạy chỉ dùng đường dẫn mới sau khi dừng rồi khởi động lại.

## Không đóng gói

- Model AI (checkpoint, LoRA, …)
- ComfyUI và venv của nó
- Blender
- Codex CLI, Claude Code CLI. File `claude.exe` (~260 MB) đi kèm `claude-agent-sdk` bị loại khỏi bản build, nên các bước dùng Claude cần `claude` có trong PATH.

Máy khác muốn chạy thì tự cài các thứ trên rồi trỏ đường dẫn trong Cài đặt.

## Ký số

Chưa ký số nên SmartScreen sẽ báo "Windows protected your PC" → **More info → Run anyway**. Có chứng chỉ thì set `CSC_LINK` (file .pfx) và `CSC_KEY_PASSWORD` trước `npm run dist:app`, electron-builder tự ký cả installer lẫn exe. Muốn ký luôn `studio-core.exe` thì ký `core/dist/studio-core/studio-core.exe` bằng `signtool` trước bước `dist:app`.

## Lỗi hay gặp

- **`dist:app` báo `ENOENT … studio-core.exe`**: chưa build core → `npm run dist:core`.
- **App báo "Không tìm thấy file chạy core"**: bản cài thiếu `resources\core` → build lại core rồi `dist:app`.
- **Core không sẵn sàng sau 60 giây ở lần mở đầu**: Defender đang quét `_internal\`. Mở lại app, hoặc thêm thư mục cài vào exclusion.
- **Core chạy nhưng thiếu module (`ModuleNotFoundError`)**: chạy thẳng `win-unpacked\resources\core\studio-core.exe serve` để xem log, thêm module vào `hiddenimports` trong `core/packaging/studio-core.spec`.
- **ComfyUI / Blender "down"**: mở Cài đặt → Đường dẫn công cụ, sửa đến khi hết dấu đỏ.
- **Log**: core spawn bởi MCP ghi `data\logs\mcp-core.log`, MCP server ghi `data\logs\mcp.log`.
- **PyInstaller báo file bị khoá khi `--clean`**: tắt app / `studio-core.exe` đang chạy từ `core/dist` rồi build lại.

## Bản portable (không cần cài)

```
cd app
npm run dist:portable
```

Chép `app/release/<version>/win-unpacked/` ra thư mục bất kỳ và tạo file rỗng tên `portable` cạnh `AI Asset Studio.exe`.
Khi có file này, app lưu data ở `<thư mục>\data` và trạng thái Electron ở `<thư mục>\userdata` thay cho `%APPDATA%`;
lệnh kết nối MCP trong app tự thêm `--data-dir` trỏ vào đó. Bản đang dùng: `L:\AI\AI Asset Studio Portable`.
