"""Scene soundscape export (op.soundscape): Audio/<asset>_soundscape.json pointing at the exported clip files, plus
README lines with the suggested AudioSource (Unity) / AudioStreamPlayer (Godot) settings per clip."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from studio_core.export.source import Files, Source

ROLE_VI = {"music": "nhạc nền", "music_tense": "nhạc căng thẳng", "ambience": "ambience", "sfx": "hiệu ứng"}


def _items(src: Source) -> list[dict[str, Any]]:
    by_stem = {Path(n).stem: n for n, _ in src.audio}
    out = []
    for it in (src.soundscape or {}).get("items") or []:
        name = by_stem.get(str(it.get("stem")))
        if name is None:
            continue
        out.append({"role": it.get("role"), "label": it.get("label"), "file": f"Audio/{name}",
                    "volume_db": it.get("volume_db"), "loop": bool(it.get("loop")), "seconds": it.get("seconds"),
                    "prompt": it.get("prompt"), **({"bpm_estimated": it["bpm_estimated"]}
                                                   if it.get("bpm_estimated") else {})})
    return out


def doc_json(src: Source) -> bytes:
    sc = src.soundscape or {}
    doc = {"scene": sc.get("scene", ""), "bpm": sc.get("bpm"), "keyscale": sc.get("keyscale"),
           "items": _items(src), "checks": sc.get("checks") or {}}
    return json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8")


def _linear(db: Any) -> float:
    try:
        return round(10 ** (float(db) / 20), 2)
    except (TypeError, ValueError):
        return 1.0


def _bpm_line(src: Source) -> str:
    sc = src.soundscape or {}
    check = (sc.get("checks") or {}).get("bpm") or {}
    warn = "" if not check or check.get("ok") else f" CẢNH BÁO: {check.get('message')}."
    return f"  Nhạc: {sc.get('bpm')} BPM, {sc.get('keyscale')}.{warn}"


def unity_files(src: Source) -> tuple[Files, list[str]]:
    items = _items(src)
    if not items:
        return [], []
    rel = f"Audio/{src.asset}_soundscape.json"
    lines = [f"- {rel}: soundscape của cảnh (vai trò, file, volume dB gợi ý, loop). Gợi ý AudioSource:",
             _bpm_line(src)]
    for it in items:
        name = it["file"].removeprefix("Audio/")
        if it["role"] == "sfx":
            how = "Loop tắt, Play On Awake tắt, gọi PlayOneShot; Spatial Blend 1 nếu phát tại vật thể"
        else:
            how = ("Loop bật, Play On Awake bật, Spatial Blend 0 (2D), Load Type = Streaming"
                   if it["role"].startswith("music") else "Loop bật, Play On Awake bật, Spatial Blend 0")
        lines.append(f"  · {name} ({ROLE_VI.get(it['role'], it['role'])}): Volume {_linear(it['volume_db'])} "
                     f"({it['volume_db']} dB); {how}.")
    if sum(1 for it in items if str(it["role"]).startswith("music")) > 1:
        lines.append("  Hai bản nhạc cùng BPM/giọng/độ dài: phát cả hai cùng PlayScheduled(dspTime) rồi crossfade "
                     "Volume khi vào/ra giao tranh.")
    return [(rel, doc_json(src))], ["\n".join(lines)]


def godot_files(src: Source) -> tuple[Files, list[str]]:
    items = _items(src)
    if not items:
        return [], []
    rel = f"Audio/{src.asset}_soundscape.json"
    lines = [f"- {rel}: soundscape của cảnh (vai trò, file, volume dB gợi ý, loop). Gợi ý AudioStreamPlayer:",
             _bpm_line(src)]
    for it in items:
        name = it["file"].removeprefix("Audio/")
        how = ("autoplay = false, gọi play() khi xảy ra; AudioStreamPlayer2D/3D nếu phát tại vật thể"
               if it["role"] == "sfx" else "autoplay = true, bật Loop ở tab Import")
        bus = "SFX" if it["role"] == "sfx" else "Music" if str(it["role"]).startswith("music") else "Ambience"
        lines.append(f"  · {name} ({ROLE_VI.get(it['role'], it['role'])}): volume_db = {it['volume_db']}, "
                     f"bus = {bus}; {how}.")
    if sum(1 for it in items if str(it["role"]).startswith("music")) > 1:
        lines.append("  Hai bản nhạc cùng BPM/giọng/độ dài: gộp vào AudioStreamSynchronized (Godot 4.3+) rồi đổi "
                     "set_sync_stream_volume() để crossfade khi vào/ra giao tranh.")
    return [(rel, doc_json(src))], ["\n".join(lines)]
