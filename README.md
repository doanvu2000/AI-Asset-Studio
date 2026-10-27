# AI Asset Studio

App Windows tạo asset game bằng AI, chạy ngay trên máy: gõ mô tả → chọn ảnh ưng → nhận sprite, texture, tileset, model 3D
hoặc nhân vật Spine có xương, xuất thẳng cho Unity / Godot / Spine.

![Tổng quan](docs/screenshots/dashboard.png)

- [Làm được gì](#làm-được-gì)
- [Cài đặt](#cài-đặt)
- [Dùng lần đầu](#dùng-lần-đầu)
- [Lỗi hay gặp](#lỗi-hay-gặp)

## Làm được gì

| Muốn | Quy trình | Ra cái gì |
|------|-----------|-----------|
| Ảnh vật phẩm / nhân vật nền trong | **Concept 2D** (Z-Image, Codex, hoặc *ý tưởng → brief* để Claude viết prompt giúp) | PNG nền trong suốt |
| Nhiều biến thể từ ảnh có sẵn | **Biến thể 2D (Flux.2 Klein)** | Biến thể + sprite sheet |
| Texture lát liền mạch | **Texture liền mạch** | Texture + điểm mối nối, normal map, bản pixel art |
| Bộ ô nền (cỏ / đất…) | **Tileset liền mạch** | Ô chung mép, hoặc bộ Wang 16 ô từ 2 vật liệu |
| Nhân vật nhìn 8 hướng | **Sprite 8 hướng** (2D nhanh từ ảnh, hoặc 3D qua mesh: đều hơn) | Sprite sheet 8 hướng + JSON, tuỳ chọn normal map / pixel art |
| Nhân vật biết đứng / đi / chạy / đánh | **Sprite 8 hướng hoạt ảnh** | Clip 8 hướng, kèm normal |
| Nhân vật Spine có xương | **Nhân vật Spine** (hoặc *trước + sau*) | Spine 4.2: JSON + atlas + PNG, 6 animation |
| Vật thể 3D | **Prop 3D (TRELLIS)** hoặc **Procedural 3D** (Claude viết code Blender) | GLB đã dọn + ảnh xoay vòng |

Mẹo prompt: viết tiếng Anh, mô tả vật thể trước, phong cách sau. Chip gợi ý dưới ô prompt thêm sẵn chất liệu / phong
cách. Muốn cả loạt asset cùng phong cách → tạo **Dự án**, khoá style + palette pixel ở đó.

Sprite hiệp sĩ 8 hướng (đánh 45°, đi 0°) dựng qua mesh 3D:

![Clip tấn công 45°](docs/screenshots/knight-attack-45.png)
![Clip đi bộ 0°](docs/screenshots/knight-walk-0.png)

Nhân vật Spine tự tách part + gắn xương; vũ khí cầm tay đi theo bàn tay:

![Khung hình Spine: viking và cung thủ](docs/screenshots/spine-frames.png)
### Một lượt chạy

Mỗi lượt chạy là một chuỗi bước, mỗi bước ghi rõ chạy ở đâu (GPU / Blender / CPU / Cloud / Thủ công) và mất bao lâu.
Kết quả hiện ngay bên phải: xoay hướng, đổi tốc độ, phóng to, bật lưới pixel.

![Chi tiết lượt chạy sprite](docs/screenshots/run-sprite.png)

- **Bước "Thủ công"** (chọn ảnh, duyệt kết quả) dừng chờ bạn. Mọi bước đang chờ gom ở **Cần duyệt**.
- Bước lỗi → bấm **Thử lại** trên bước đó, không phải chạy lại từ đầu.
- **Xuất cho engine**: Unity (sprite đã cắt, animation 8 hướng, prefab), Godot 4 (SpriteFrames, scene, TileSet),
  Spine (JSON + atlas + PNG), GLB.

Texture và tileset có chế độ xem lát 3×3 / 5×5 để soi mối nối:

![Tileset Wang cỏ / đất](docs/screenshots/run-tileset-wang.png)
![Texture gạch lát 3×3](docs/screenshots/run-texture-bricks.png)

**Thư viện** giữ mọi asset theo loại, lọc nháp / đã duyệt / từ chối, theo dự án:

![Thư viện](docs/screenshots/library.png)

Màn khác: **Hàng loạt** (nhiều prompt một lần), **Quy trình** (xem + chỉnh các bước), **Dự án**. Phím tắt `Ctrl+1..8`
đổi màn, `Ctrl+K` tìm mọi thứ.

## Cài đặt

### 1. Máy cần có

| Thứ | Ghi chú |
|-----|---------|
| Windows 10/11 x64, GPU NVIDIA ≥ 12 GB VRAM | Máy chuẩn: RTX 5070 12 GB, 64 GB RAM. Quy trình hoạt ảnh dùng đỉnh ~28 GB RAM |
| ComfyUI 0.41 + model (~80 GB, nên để ổ SSD) | Chạy model ảnh / 3D. Cách cài ở bước 2 |
| Blender 5.2 | Chỉ cần cho quy trình 3D và sprite 8 hướng 3D |
| Claude Code (`claude`) | Cho các bước "Claude viết brief / soát khớp xương". Đăng nhập một lần bằng `claude` |
| Codex CLI | Tuỳ chọn, chỉ cho `Concept 2D (Codex)` |

ComfyUI, model, Blender không đi kèm app (quá nặng); cài một lần rồi trỏ đường dẫn ở bước 4.

### 2. Cài ComfyUI + model

Cần [git](https://git-scm.com/download/win) và [uv](https://docs.astral.sh/uv/getting-started/installation/). Mở
PowerShell:

```powershell
git clone --branch v0.41.0 https://github.com/comfy-org/ComfyUI D:\AI\comfyUI
cd D:\AI\comfyUI
uv venv --python 3.13 .venv
uv pip install --python .venv torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130
uv pip install --python .venv -r requirements.txt
```

Tải model (22 file ~80 GB, tải dở chạy lại lệnh là tải tiếp):

```powershell
powershell -ExecutionPolicy Bypass -File setup\comfyui-models.ps1 -Root E:\AI\comfyUI_models
```

Workflow ComfyUI có sẵn trong thư mục `setup\workflows` của dự án.

### 3. Mở app

- **Bản portable** (khuyên dùng): chép thư mục `AI Asset Studio Portable` ra đâu cũng được, chạy `AI Asset Studio.exe`.
  Dữ liệu nằm ngay trong thư mục đó (`data\`), mang sang máy khác vẫn còn.
- **Bản cài**: chạy `AI-Asset-Studio-Setup-<version>-x64.exe`. Dữ liệu ở `%APPDATA%\AI Asset Studio\data`.

SmartScreen báo "Windows protected your PC" → **More info → Run anyway** (app chưa ký số).

### 4. Trỏ đường dẫn công cụ

**Cài đặt → Đường dẫn công cụ** → bấm **Chọn…** từng dòng → **Kiểm tra lại** đến khi tất cả có dấu ✓ xanh → **Lưu**.

![Đường dẫn công cụ](docs/screenshots/settings-tools.png)

Không cần tự bật ComfyUI / Blender: app tự khởi động khi lượt chạy cần, và tắt khi đóng app. Thanh trên cùng của
**Tổng quan** cho biết trạng thái từng công cụ.

## Dùng lần đầu

1. Bấm **Tạo mới** (`Ctrl+N` hoặc nút xanh góc phải).
2. Chọn một quy trình, ví dụ **Concept 2D (Z-Image)**.
3. Gõ prompt tiếng Anh, vd. `crystal sword with a dragon hilt, game item`, bấm **Bắt đầu chạy**.
4. Khi app sinh xong vài ảnh, mục **Cần duyệt** sáng lên → bấm chọn ảnh ưng nhất.
5. Lượt chạy làm nốt (tách nền, căn chỉnh) → bấm **Đưa vào thư viện** hoặc **Xuất… / Xuất cho engine**.

![Chọn quy trình](docs/screenshots/new-run.png)

## Lỗi hay gặp

| Triệu chứng | Cách xử lý |
|-------------|------------|
| Dòng ComfyUI / Blender có dấu đỏ | **Cài đặt → Đường dẫn công cụ**, sửa đường dẫn, **Kiểm tra lại**, rồi khởi động lại công cụ ở **Tổng quan** |
| Bước Claude lỗi | Chạy `claude` trong terminal một lần để đăng nhập, rồi **Thử lại** bước đó |
| Lần mở đầu báo core chưa sẵn sàng sau 60 giây | Defender đang quét; mở lại app hoặc thêm thư mục app vào exclusion |
| GPU hết bộ nhớ / ComfyUI tắt giữa chừng | Đóng app nặng khác (game, trình duyệt nhiều tab), chạy từng lượt một |
| Cần gửi log báo lỗi | **Cài đặt → Nhật ký chẩn đoán → Copy** |

Tự build từ mã nguồn: [BUILD.md](BUILD.md).
