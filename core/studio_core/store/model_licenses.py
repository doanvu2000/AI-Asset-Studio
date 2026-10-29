"""Licenses of the generators a run can use, for a pack's provenance.json.

The bootstrap manifest's `license` / `commercial` fields win when present; this table is the fallback (and the
source for entries the manifest does not list). `commercial` is one of yes | restricted
(allowed under conditions: revenue caps, attribution, custom terms) | no | unknown (not verified, check before
shipping a commercial game). Keys are model file names from bootstrap/manifest.json or a generator kind.
"""
from __future__ import annotations

from typing import Any

# (license, commercial, source)
MODELS: dict[str, tuple[str, str, str]] = {
    "RealESRGAN_x4plus.safetensors": ("BSD-3-Clause", "yes", "xinntao/Real-ESRGAN"),
    "birefnet.safetensors": ("MIT", "yes", "ZhengPeng7/BiRefNet"),
    "ae.safetensors": ("Apache-2.0", "yes", "Tongyi-MAI/Z-Image-Turbo"),
    "z_image_turbo_bf16.safetensors": ("Apache-2.0", "yes", "Tongyi-MAI/Z-Image-Turbo"),
    "qwen_3_4b.safetensors": ("Apache-2.0", "yes", "Qwen/Qwen3-4B"),
    "Z-Image-Turbo-Fun-Controlnet-Union.safetensors": ("Apache-2.0", "yes", "alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union"),
    "moge_2_vitl_normal_fp16.safetensors": ("MIT", "unknown", "microsoft/MoGe (MoGe-2 weights: verify)"),
    "trellis_2_int8_convrot.safetensors": ("MIT", "yes", "microsoft/TRELLIS.2"),
    "trellis_2_shape_vae_bf16.safetensors": ("MIT", "yes", "microsoft/TRELLIS.2"),
    "trellis_2_texture_vae_bf16.safetensors": ("MIT", "yes", "microsoft/TRELLIS.2"),
    "pixal3d_int8_convrot.safetensors": ("unverified", "unknown", "Pixal3D"),
    "pixal3d_multiview_int8_convrot.safetensors": ("unverified", "unknown", "Pixal3D"),
    "dino_v3_L_naf_fp32.safetensors": ("DINOv3 License (custom)", "restricted", "facebook/dinov3"),
    "flux-2-klein-base-4b-fp8.safetensors": ("Apache-2.0", "yes", "black-forest-labs/FLUX.2-klein-4B"),
    "full_encoder_small_decoder.safetensors": ("Apache-2.0", "yes", "black-forest-labs/FLUX.2-small-decoder"),
    "qwen_image_edit_2511_int8_convrot.safetensors": ("Apache-2.0", "yes", "Qwen/Qwen-Image-Edit-2511"),
    "qwen_2.5_vl_7b_fp8_scaled.safetensors": ("Apache-2.0", "yes", "Qwen/Qwen2.5-VL-7B-Instruct"),
    "qwen_image_vae.safetensors": ("Apache-2.0", "yes", "Qwen/Qwen-Image"),
    "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors": ("Apache-2.0", "yes", "lightx2v/Qwen-Image-Lightning"),
    "qwen-image-edit-2511-multiple-angles-lora.safetensors": ("unverified", "unknown", "fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA"),
    "sam3.1_multiplex_fp16.safetensors": ("SAM License (custom)", "restricted", "facebook/sam3"),
    "stable-audio-open-1.0.safetensors": ("Stability AI Community License", "restricted",
                                          "stabilityai/stable-audio-open-1.0 (commercial only under USD 1M revenue)"),
    "t5-base.safetensors": ("Apache-2.0", "yes", "google-t5/t5-base"),
    "acestep_v1.5_turbo.safetensors": ("unverified", "unknown", "ACE-Step 1.5"),
    "qwen_0.6b_ace15.safetensors": ("unverified", "unknown", "ACE-Step 1.5"),
    "qwen_1.7b_ace15.safetensors": ("unverified", "unknown", "ACE-Step 1.5"),
    "ace_1.5_vae.safetensors": ("unverified", "unknown", "ACE-Step 1.5"),
    "wan2.1_t2v_14B_fp8_scaled.safetensors": ("Apache-2.0", "yes", "Wan-AI/Wan2.1-T2V-14B"),
    "umt5_xxl_fp8_e4m3fn_scaled.safetensors": ("Apache-2.0", "yes", "google/umt5-xxl"),
    "wan_alpha_2.1_rgba_lora.safetensors": ("unverified", "unknown", "Wan-Alpha"),
    "wan_alpha_2.1_vae_rgb_channel.safetensors": ("unverified", "unknown", "Wan-Alpha"),
    "wan_alpha_2.1_vae_alpha_channel.safetensors": ("unverified", "unknown", "Wan-Alpha"),
    "lightx2v_T2V_14B_cfg_step_distill_v2_lora_rank64_bf16.safetensors": ("Apache-2.0", "yes", "lightx2v/Wan2.1-T2V-14B-StepDistill"),
    "wan2.2_ti2v_5B_fp16.safetensors": ("Apache-2.0", "yes", "Wan-AI/Wan2.2-TI2V-5B"),
    "wan2.2_vae.safetensors": ("Apache-2.0", "yes", "Wan-AI/Wan2.2-TI2V-5B"),
    "vits-piper-vi_VN-vais1000-medium": ("unverified (VAIS-1000 dataset terms)", "unknown", "rhasspy/piper-voices"),
    "kokoro-int8-multi-lang-v1_0": ("Apache-2.0", "yes", "hexgrad/Kokoro-82M"),
}

# Hosted generators: their output terms, not a weights license.
SERVICES: dict[str, tuple[str, str, str]] = {
    "codex": ("OpenAI Terms of Use (output owned by the user)", "yes", "OpenAI image generation via Codex"),
    "claude": ("Anthropic Commercial Terms (output owned by the user)", "yes", "Claude (text/vision only)"),
}

_RANK = {"yes": 0, "restricted": 1, "unknown": 2, "no": 3}


def _manifest_entry(name: str) -> dict[str, Any] | None:
    from studio_core.bootstrap.plan import manifest

    return next((m for m in manifest()["models"] if m["name"] == name and m.get("license")), None)


def model_license(name: str) -> dict[str, Any]:
    lic, commercial, source = MODELS.get(name) or SERVICES.get(name) or ("unverified", "unknown", "")
    entry = _manifest_entry(name)
    if entry:
        lic = entry["license"]
        commercial = entry.get("commercial") or (commercial if name in MODELS else "unknown")
        source = entry.get("source") or source or entry.get("url", "")
    return {"name": name, "license": lic, "commercial": commercial, "source": source}


def worst(values: list[str]) -> str:
    return max(values, key=lambda v: _RANK.get(v, 2), default="yes")
