# Cài venv riêng (CPU) cho normal map AI: MoGe-2 ViT-S normal (MIT). Chạy lại được; không động core\.venv.
# App cài gói này qua Thiết lập (vào <thư mục model>
ormal-ai); script dùng khi cài tay, trỏ app tới đây bằng STUDIO_NORMAL_AI_DIR.
# Dùng: powershell -ExecutionPolicy Bypass -File setup\normal-ai.ps1 [-Root L:\AI\setup\normal-ai]
param([string]$Root = 'L:\AI\setup\normal-ai')
$ErrorActionPreference = 'Stop'
# Ổ C: gần đầy nên cache/tmp của uv và HF đều nằm trong $Root.
$env:UV_CACHE_DIR = "$Root\uv-cache"; $env:TMP = "$Root\tmp"; $env:TEMP = "$Root\tmp"; $env:HF_HOME = "$Root\hf"
New-Item -ItemType Directory -Force $Root, "$Root\tmp", "$Root\src" | Out-Null
$py = "$Root\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { uv venv --python 3.11 "$Root\.venv" }
uv pip install --python $py torch torchvision --index-url https://download.pytorch.org/whl/cpu
uv pip install --python $py numpy pillow scipy opencv-python-headless huggingface_hub click "git+https://github.com/EasternJournalist/utils3d.git@3fab839f0be9931dac7c8488eb0e1600c236e183"
# Mã MoGe v2 (commit b942f00, MIT); main đã sang v3 nên ghim commit.
if (-not (Test-Path "$Root\src\moge\moge")) {
  git clone https://github.com/microsoft/MoGe.git "$Root\src\moge"
  git -C "$Root\src\moge" checkout b942f00
}
& $py -I -c "import sys; sys.path.insert(0, r'$Root\src\moge'); from moge.model.v2 import MoGeModel; MoGeModel.from_pretrained('Ruicheng/moge-2-vits-normal')"
