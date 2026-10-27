# Tải model ComfyUI cho AI Asset Studio. Chạy lại lúc nào cũng được: file xong thì bỏ qua, file dở thì tải tiếp.
# Dùng: powershell -ExecutionPolicy Bypass -File setup\comfyui-models.ps1 -Root E:\AI\comfyUI_models
param([Parameter(Mandatory)][string]$Root)
$ErrorActionPreference = 'Continue'
$root = $Root
$log = Join-Path $root 'download.log'
$hf = 'https://huggingface.co'

$models = @(
    # A. support
    @('background_removal', "$hf/Comfy-Org/BiRefNet/resolve/main/background_removal/birefnet.safetensors"),
    @('upscale_models',     "$hf/Comfy-Org/Real-ESRGAN_repackaged/resolve/main/RealESRGAN_x4plus.safetensors"),
    @('vae',                "$hf/Comfy-Org/z_image_turbo/resolve/main/split_files/vae/ae.safetensors"),
    @('vae',                "$hf/Comfy-Org/flux2-dev/resolve/main/split_files/vae/flux2-vae.safetensors"),
    @('geometry_estimation',"$hf/Comfy-Org/MoGe/resolve/main/geometry_estimation/moge_2_vitl_normal_fp16.safetensors"),
    # B. 3D
    @('checkpoints',        "$hf/Comfy-Org/hunyuan3D_2.1_repackaged/resolve/main/hunyuan_3d_v2.1.safetensors"),
    @('diffusion_models',   "$hf/Comfy-Org/TRELLIS.2/resolve/main/diffusion_models/trellis_2_int8_convrot.safetensors"),
    @('diffusion_models',   "$hf/Comfy-Org/Pixal3D/resolve/main/diffusion_models/pixal3d_int8_convrot.safetensors"),
    @('clip_vision',        "$hf/Comfy-Org/Pixal3D/resolve/main/clip_vision/dino_v3_L_naf_fp32.safetensors"),
    @('vae',                "$hf/Comfy-Org/Pixal3D/resolve/main/vae/trellis_2_shape_vae_bf16.safetensors"),
    @('vae',                "$hf/Comfy-Org/Pixal3D/resolve/main/vae/trellis_2_texture_vae_bf16.safetensors"),
    # C. 2D generation
    @('diffusion_models',   "$hf/Comfy-Org/z_image_turbo/resolve/main/split_files/diffusion_models/z_image_turbo_bf16.safetensors"),
    @('text_encoders',      "$hf/Comfy-Org/z_image_turbo/resolve/main/split_files/text_encoders/qwen_3_4b.safetensors"),
    @('model_patches',      "$hf/alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union/resolve/main/Z-Image-Turbo-Fun-Controlnet-Union.safetensors"),
    @('diffusion_models',   "$hf/black-forest-labs/FLUX.2-klein-base-4b-fp8/resolve/main/flux-2-klein-base-4b-fp8.safetensors"),
    @('vae',                "$hf/black-forest-labs/FLUX.2-small-decoder/resolve/main/full_encoder_small_decoder.safetensors"),
    # D. multi-angle sprites + segmentation
    @('diffusion_models',   "$hf/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_edit_2511_int8_convrot.safetensors"),
    @('text_encoders',      "$hf/Comfy-Org/HunyuanVideo_1.5_repackaged/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors"),
    @('vae',                "$hf/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors"),
    @('loras',              "$hf/lightx2v/Qwen-Image-Edit-2511-Lightning/resolve/main/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"),
    @('loras',              "$hf/fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA/resolve/main/qwen-image-edit-2511-multiple-angles-lora.safetensors"),
    @('checkpoints',        "$hf/Comfy-Org/sam3.1/resolve/main/checkpoints/sam3.1_multiplex_fp16.safetensors")
)

function Write-Log($msg) { Add-Content -Path $log -Value ("{0:HH:mm:ss} {1}" -f (Get-Date), $msg) -Encoding utf8 }

function Get-RemoteSize($url) {
    $len = curl.exe -sIL $url | Select-String '^content-length:' | Select-Object -Last 1
    if ($len) { [int64]$len.ToString().Split(':')[1].Trim() } else { -1 }
}

$ok = 0; $fail = 0
New-Item -ItemType Directory -Force $root | Out-Null
Write-Log "START $($models.Count) files"
foreach ($m in $models) {
    $dir = Join-Path $root $m[0]
    $url = $m[1]
    $name = ($url -split '/')[-1]
    $dest = Join-Path $dir $name
    New-Item -ItemType Directory -Force $dir | Out-Null

    $remote = Get-RemoteSize $url
    $local = if (Test-Path $dest) { (Get-Item $dest).Length } else { 0 }
    if ($remote -gt 0 -and $local -eq $remote) { Write-Log "SKIP $($m[0])/$name"; $ok++; continue }
    if ($remote -gt 0 -and $local -gt $remote) { Remove-Item $dest -Force; $local = 0 }

    Write-Log ("GET  {0}/{1} {2:N2} GB" -f $m[0], $name, ($remote / 1GB))
    $t0 = Get-Date
    curl.exe -L -C - --retry 20 --retry-delay 10 --retry-all-errors --speed-limit 1024 --speed-time 120 -sS -o $dest $url
    $local = if (Test-Path $dest) { (Get-Item $dest).Length } else { 0 }
    if ($remote -gt 0 -and $local -eq $remote) {
        $mbps = $local / 1MB / [math]::Max(1, ((Get-Date) - $t0).TotalSeconds)
        Write-Log ("OK   {0}/{1} ({2:N1} MB/s)" -f $m[0], $name, $mbps)
        $ok++
    } else {
        Write-Log ("FAIL {0}/{1} local={2} remote={3}" -f $m[0], $name, $local, $remote)
        $fail++
    }
}
Write-Log "DONE ok=$ok fail=$fail"
