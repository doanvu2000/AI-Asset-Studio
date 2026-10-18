# Tích hợp ComfyUI local qua API

Tài liệu cho project khác (phần mềm quản lý) điều khiển ComfyUI cài sẵn trên máy này qua HTTP + WebSocket.
Không dùng ComfyUI desktop app, không dùng UI web — chỉ API.
Danh mục workflow + điểm cắm: [COMFYUI-WORKFLOWS.md](COMFYUI-WORKFLOWS.md).

## 1. Môi trường

| Mục | Giá trị |
|---|---|
| Máy | RTX 5070 12GB VRAM (sm_120), RAM 64GB, Windows 11 |
| ComfyUI | 0.41.0 (git clone, commit `ce1e922d`) tại `L:\AI\setup\comfyUI` |
| Python | 3.13.14, venv `L:\AI\setup\comfyUI\.venv` (uv), torch 2.14.1+cu130 |
| Model | `F:\work\AI\comfyUI_models\<loại>\` (NVMe, 22 file ~80GB) |
| Ảnh vào | `L:\AI\setup\comfyUI\input\` |
| Kết quả | `L:\AI\setup\comfyUI\output\` (`sprites\`, `mesh\`, `3d\`, …) |
| Workflow API | `L:\AI\setup\workflows\*.api.json` + `index.json` |
| Server | `http://127.0.0.1:8188` (chỉ localhost) |

## 2. Chạy / dừng server

```powershell
L:\AI\setup\run-comfyui.ps1                          # 127.0.0.1:8188
L:\AI\setup\run-comfyui.ps1 -Port 8190               # đổi port
L:\AI\setup\run-comfyui.ps1 -Listen 0.0.0.0          # cho máy khác trong LAN gọi
L:\AI\setup\run-comfyui.ps1 -Manager                 # bật ComfyUI-Manager
L:\AI\setup\run-comfyui.ps1 -Extra '--preview-method','auto'   # preview ảnh qua WS khi đang sample
```

Launcher luôn thêm `--disable-auto-launch --disable-pinned-memory --models-directory F:\work\AI\comfyUI_models`.

Phần mềm quản lý tự spawn server thì gọi trực tiếp (không cần PowerShell):

```
L:\AI\setup\comfyUI\.venv\Scripts\python.exe main.py --listen 127.0.0.1 --port 8188 --disable-auto-launch --disable-pinned-memory --models-directory F:\work\AI\comfyUI_models
cwd = L:\AI\setup\comfyUI
```

- **Sẵn sàng**: poll `GET /system_stats` tới khi 200 (khởi động ~60s, đo 2026-10-18 — đặt timeout ≥ 120s).
  Kiểm tra `system.argv` có `--disable-pinned-memory` và `--models-directory`.
- **Dừng**: kill process (không có endpoint shutdown). Python trong venv uv là launcher → kill cả cây process
  (`taskkill /T /F /PID <pid>`), không thì process con giữ VRAM.
- **Bắt buộc `--disable-pinned-memory`** — xem mục 7.

## 3. Luồng gọi một job

```
upload ảnh ─► sửa JSON workflow ─► POST /prompt ─► WS progress ─► GET /history/{id} ─► GET /view (tải file)
```

### 3.1 Upload ảnh đầu vào

`POST /upload/image` — multipart/form-data:

| Field | Ý nghĩa |
|---|---|
| `image` | file (bắt buộc) |
| `subfolder` | thư mục con trong `input\` (tuỳ chọn) |
| `type` | `input` (mặc định) / `temp` / `output` |
| `overwrite` | `"true"` ghi đè; bỏ trống thì trùng tên → tự đổi `name (1).png` (trùng hash thì dùng lại file cũ) |

Response: `{"name": "x.png", "subfolder": "", "type": "input", ...}` → dùng `name` (kèm `subfolder/` nếu có) làm giá trị
`LoadImage.image`. Có thể bỏ qua upload bằng cách copy file thẳng vào `L:\AI\setup\comfyUI\input\` (cùng máy).

### 3.2 Sửa workflow

Workflow API là dict `{"<node_id>": {"class_type", "inputs", "_meta"}}`. Node id có thể dạng `"57:27"` (node trong subgraph).
Điểm cắm lấy từ `index.json`, key dạng `"<node_id>.<input>"`:

```python
node_id, name = key.rsplit(".", 1)
prompt[node_id]["inputs"][name] = value
```

- Giá trị là list `["node_id", slot]` = link tới node khác — **không** ghi đè bằng scalar trừ khi chủ ý.
- **Seed**: workflow lưu seed cố định. Muốn ảnh khác mỗi lần thì random seed (0 … 2^53) vào mọi key trong `seeds`.
- Đổi `filename_prefix` của node Save* để phân thư mục kết quả (`"sprites/car_red"` → `output\sprites\car_red_00001_.png`).

### 3.3 Gửi job

`POST /prompt`

```json
{"prompt": { ...workflow... }, "client_id": "<uuid của app>", "prompt_id": "<uuid tuỳ chọn>"}
```

- `client_id`: id kết nối WS của app — server chỉ gửi event chi tiết cho WS có cùng `clientId`.
- `prompt_id`: tự sinh UUID (lowercase, có gạch) để biết id trước khi gửi; bỏ trống thì server sinh.
- `"front": true` để chen lên đầu hàng đợi.

Response 200: `{"prompt_id": "...", "number": 3, "node_errors": {...}}`
Response 400: `{"error": {"type", "message", "details"}, "node_errors": {...}}` — khi **không** output nào hợp lệ.

> ⚠️ **Bẫy**: 200 vẫn có thể kèm `node_errors` không rỗng. Server chạy các output hợp lệ, **lặng lẽ bỏ** output lỗi,
> history vẫn báo `success`. Luôn kiểm tra `node_errors` ngay khi gửi:
> `node_errors[<node_id>] = {"errors": [{"type","message","details"}], "class_type", "dependent_outputs"}`.

### 3.4 Theo dõi tiến độ — WebSocket

`ws://127.0.0.1:8188/ws?clientId=<client_id>` — mở **trước** khi POST /prompt. Frame text là JSON `{"type", "data"}`:

| type | data | Ý nghĩa |
|---|---|---|
| `status` | `{status: {exec_info: {queue_remaining}}}` | số job còn trong hàng đợi |
| `execution_start` | `{prompt_id}` | job bắt đầu chạy |
| `execution_cached` | `{prompt_id, nodes: [...]}` | node dùng lại kết quả cache (input không đổi) |
| `executing` | `{prompt_id, node}` | đang chạy node `node`; **`node: null` = job xong** |
| `progress` | `{prompt_id, node, value, max}` | tiến độ step trong node (sampler, decode…) |
| `executed` | `{prompt_id, node, output}` | node output xong, `output` giống `history.outputs[node]` |
| `execution_success` | `{prompt_id}` | job thành công |
| `execution_error` | `{prompt_id, node_id, node_type, exception_message, exception_type, traceback}` | job lỗi |
| `execution_interrupted` | `{prompt_id, node_id}` | bị huỷ |

Frame binary = ảnh preview khi chạy với `--preview-method auto` (4 byte type + 4 byte format + JPEG/PNG).
Mất WS thì fallback poll `GET /history/{prompt_id}` mỗi 1–2s.

### 3.5 Lấy kết quả

`GET /history/{prompt_id}` → `{"<prompt_id>": {"status": {...}, "outputs": {...}}}` (rỗng `{}` khi chưa xong).

- `status.completed` (bool), `status.status_str` (`success` / `error`), `status.messages` = list `[type, data]`
  (có `execution_error` khi lỗi).
- `outputs[<node_id>]`:
  - Ảnh (`SaveImage`): `{"images": [{"filename", "subfolder", "type": "output"}]}`
  - 3D (`SaveGLB`, `Save3DAdvanced`): `{"3d": [{"filename", "subfolder", "type": "output"}], ...}`
- Bỏ qua output `type: "temp"` (PreviewImage) — chỉ là preview.

Tải file: `GET /view?filename=<f>&subfolder=<s>&type=output` (cùng máy thì đọc thẳng
`L:\AI\setup\comfyUI\output\<subfolder>\<filename>`).

### 3.6 Endpoint khác

| Endpoint | Việc |
|---|---|
| `GET /system_stats` | health check + VRAM/RAM trống, version |
| `GET /queue` | `{queue_running: [...], queue_pending: [...]}` |
| `POST /queue` `{"delete": [prompt_id]}` / `{"clear": true}` | bỏ job đang chờ |
| `POST /api/jobs/{prompt_id}/cancel` | huỷ 1 job (đang chạy hoặc đang chờ) → `{"cancelled": bool}` |
| `POST /interrupt` `{"prompt_id": "..."}` | dừng job đang chạy (bỏ `prompt_id` = dừng bất kỳ job nào đang chạy) |
| `POST /free` `{"unload_models": true, "free_memory": true}` | nhả VRAM/RAM (ví dụ trước khi Blender render GPU) |
| `POST /history` `{"delete": [id]}` / `{"clear": true}` | dọn history (history giữ trong RAM, mất khi restart) |
| `GET /object_info[/<class>]` | schema mọi node (input, kiểu, default, danh sách model) |
| `GET /models/<folder>` | danh sách file model, ví dụ `/models/diffusion_models` |

## 4. Client mẫu — Python (stdlib)

```python
import json, random, time, urllib.request, uuid

BASE = "http://127.0.0.1:8188"
WF_DIR = r"L:\AI\setup\workflows"

def http(path, payload=None, raw=False):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return r.read() if raw else json.load(r)

def upload(path):
    boundary = uuid.uuid4().hex
    name = path.replace("\\", "/").split("/")[-1]
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + open(path, "rb").read() + \
           f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(BASE + "/upload/image", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)["name"]

def run(workflow, values, timeout=1800):
    index = json.load(open(f"{WF_DIR}\\index.json", encoding="utf-8"))[workflow]
    prompt = json.load(open(f"{WF_DIR}\\{index['file']}", encoding="utf-8"))
    for key in index["seeds"]:
        values.setdefault(key, random.randint(0, 2**53))
    for key, v in values.items():
        node, name = key.rsplit(".", 1)
        prompt[node]["inputs"][name] = v
    res = http("/prompt", {"prompt": prompt, "client_id": str(uuid.uuid4())})
    if res.get("node_errors"):
        raise RuntimeError(f"invalid nodes: {res['node_errors']}")
    pid, t0 = res["prompt_id"], time.time()
    while time.time() - t0 < timeout:
        h = http(f"/history/{pid}").get(pid)
        if h and h["status"].get("completed") is not None:
            if h["status"]["status_str"] != "success":
                raise RuntimeError(h["status"]["messages"])
            return [f for out in h["outputs"].values() for kind in ("images", "3d")
                    for f in out.get(kind, []) if f.get("type") == "output"]
        time.sleep(1)
    raise TimeoutError(pid)

# text → sprite → tách nền → mesh
img = run("t2i_zimage_turbo", {"57:27.text": "pixel art red race car, side view, white background"})[0]
src = upload(rf"L:\AI\setup\comfyUI\output\{img['subfolder']}\{img['filename']}")
rgba = run("remove_bg_birefnet", {"17.image": src})[0]
mesh = run("img2mesh_trellis2_textured", {"122.image": src})[0]   # GLB có texture
```

Tham khảo thêm: `L:\AI\setup\tools\run_workflow.py` (CLI tương tự, in `node_errors` + outputs).

## 5. Client mẫu — TypeScript (WebSocket tiến độ)

```ts
const BASE = "http://127.0.0.1:8188";
const clientId = crypto.randomUUID();
const ws = new WebSocket(`ws://127.0.0.1:8188/ws?clientId=${clientId}`);

function queue(prompt: object, onProgress: (node: string, v: number, max: number) => void): Promise<string> {
  const promptId = crypto.randomUUID();
  return new Promise(async (resolve, reject) => {
    ws.addEventListener("message", function handler(ev) {
      if (typeof ev.data !== "string") return;            // binary = preview image
      const { type, data } = JSON.parse(ev.data);
      if (data?.prompt_id !== promptId) return;
      if (type === "progress") onProgress(data.node, data.value, data.max);
      if (type === "execution_success") { ws.removeEventListener("message", handler); resolve(promptId); }
      if (type === "execution_error" || type === "execution_interrupted") {
        ws.removeEventListener("message", handler); reject(data);
      }
    });
    const res = await fetch(`${BASE}/prompt`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, client_id: clientId, prompt_id: promptId }),
    });
    const body = await res.json();
    if (!res.ok || Object.keys(body.node_errors ?? {}).length) reject(body);
  });
}

// sau khi resolve: GET /history/{promptId} → outputs → /view?filename=..&subfolder=..&type=output
```

## 6. Nhúng workflow vào project khác

Copy sang project mới (hoặc đọc thẳng đường dẫn L:):

- `L:\AI\setup\workflows\*.api.json` + `index.json` — gửi thẳng được, đã đổi sang model có trên máy.
- `L:\AI\setup\tools\ui2api.py` — chuyển workflow UI (file `.json` export từ ComfyUI, kể cả subgraph) → API.
  Workflow API của ComfyUI phụ thuộc phiên bản node; ComfyUI update thì chạy lại
  `comfyUI\.venv\Scripts\python.exe L:\AI\setup\tools\build_workflows.py` (server phải đang chạy) để sinh lại.
- Thêm workflow mới: thêm vào `WORKFLOWS` trong `build_workflows.py` (template có sẵn ở
  `comfyUI\.venv\Lib\site-packages\comfyui_workflow_templates_json\templates\`), hoặc
  `python ui2api.py my_workflow.json out.api.json`.
- Thiếu model: thêm dòng vào `L:\AI\setup\comfyui-models.ps1` (tải resumable thẳng vào `F:\work\AI\comfyUI_models`).

## 7. Ràng buộc vận hành (quan trọng)

- **Pinned memory phải tắt.** Windows commit limit = RAM 64GB + pagefile 4GB; app khác (Chrome, Claude, Java…)
  giữ ~40GB → pinned memory làm cấp phát thất bại → torch crash cứng `Windows fatal exception: access violation`
  (c10.dll / VCRUNTIME140.dll, 0xc0000005) thay vì báo OOM. Process chết, WS đóng, không có `execution_error`.
  App quản lý nên: phát hiện process chết → báo lỗi job + restart server. Muốn bật lại pinned memory
  (`-PinnedMemory`, nhanh hơn chút) thì phải tăng pagefile trước.
- **Một GPU, một job một lúc.** Queue chạy tuần tự. Job khác loại model thì model cũ bị đẩy khỏi VRAM → lần sau nạp lại.
  Gom job cùng workflow chạy liền nhau cho nhanh.
- **Cache theo input**: gửi lại y hệt workflow (cùng seed) → server trả cache, không chạy lại (`execution_cached`).
- **VRAM 12GB**: đã dùng bản quantized (int8/fp8) cho TRELLIS.2, Pixal3D, Qwen-Image-Edit, Flux.2 Klein.
  Đừng tăng resolution/batch tuỳ tiện; OOM thật thì có `execution_error` kiểu `torch.OutOfMemoryError`.
- **Timeout**: job 3D/multi-angle chạy 5–8 phút → timeout client ≥ 30 phút.
- **History/queue nằm trong RAM** — restart server là mất. App nên tự lưu prompt_id ↔ file output.
- **Bảo mật**: server không có auth. Giữ `--listen 127.0.0.1`; mở LAN thì chỉ trong mạng tin cậy.
- **Không có API trả phí**: các template dùng node API (Gemini, Recraft…) bị loại; sinh ảnh concept bằng Codex image gen
  rồi đưa vào ComfyUI (upload ảnh) để tách nền / dựng 3D / đổi góc.

## 8. Gợi ý cho ComfyUI adapter (AI-Asset-Studio)

Map yêu cầu "ComfyUI adapter" trong plan sang API thực tế (không phụ thuộc ngôn ngữ core — Python/Rust đều chỉ là HTTP + WS):

| Yêu cầu | Cách làm |
|---|---|
| Check connectivity | `GET /system_stats` → `system.comfyui_version`, `devices[0].vram_free` |
| Validate workflow availability | mọi `class_type` trong prompt phải có trong `GET /object_info`; input chọn file model (combo) phải nằm trong danh sách option của node đó (`object_info[cls].input.required[name][0]` là list, hoặc `["COMBO", {"options": [...]}]`) |
| Submit | `POST /prompt` kèm `prompt_id` tự sinh; `node_errors` khác rỗng = fail ngay, không chờ |
| Track progress | WS `progress` (value/max của node đang chạy), `executing` để biết node; tiến độ tổng ≈ số node đã xong / tổng node |
| Capture errors | 3 tầng: `node_errors` lúc gửi · `execution_error` (WS/history) · process server chết (crash cứng, mục 7) |
| Collect outputs | `history.outputs[*].images` / `["3d"]`, chỉ lấy `type: "output"`; **copy** (không move) vào asset store |
| Validate format | PNG: decode (Pillow / crate `image`), kiểm kích thước, sprite cần kênh alpha + góc trong suốt; GLB: 4 byte đầu `glTF`, parse bằng `trimesh` / crate `gltf` / Blender |
| Version + metadata | lưu prompt JSON đã gửi (đủ để chạy lại y hệt), tên workflow + hash file `.api.json`, seed, `comfyui_version`, thời gian chạy |
| Retry không xoá bản cũ | gửi lại với `prompt_id` mới; Save* tự tăng số (`prefix_00002_.png`), không ghi đè |
| Không hardcode 1 workflow | dùng `index.json` làm registry; mỗi workflow có `images/texts/seeds/outputs` |

## 9. Blender

- Blender 5.2.2 LTS, addon MCP `blender_mcp`, Cycles OptiX. Dữ liệu ở `L:\AI\blender` (`project\`, `renders\`, `assets\`).
- Import GLB từ ComfyUI: `bpy.ops.import_scene.gltf(filepath=r"L:\AI\setup\comfyUI\output\3d\ComfyUI_00001.glb")`.
- Mesh AI rất dày (TRELLIS ~700k mặt, Hunyuan ~400k) → Decimate trước khi làm asset game, hoặc giảm
  `186.target_face_count` ngay trong workflow TRELLIS.
- Blender render GPU cùng lúc với ComfyUI → gọi `POST /free {"unload_models": true}` trước để nhả VRAM.
