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


def _bpm_check(src: Source) -> dict[str, Any]:
    return ((src.soundscape or {}).get("checks") or {}).get("bpm") or {}


def _bpm_line(src: Source) -> str:
    sc = src.soundscape or {}
    check = _bpm_check(src)
    warn = "" if not check or check.get("ok") else f" CẢNH BÁO: {check.get('message')}."
    return f"  Nhạc: {sc.get('bpm')} BPM, {sc.get('keyscale')}.{warn}"


def _music_pair_line(src: Source, items: list[dict[str, Any]], synced: str) -> list[str]:
    if sum(1 for it in items if str(it["role"]).startswith("music")) < 2:
        return []
    check = _bpm_check(src)
    if check and not check.get("ok"):
        return ["  Hai bản nhạc LỆCH BPM (xem cảnh báo trên): đừng phát đồng bộ; chuyển bằng crossfade nối tiếp "
                "(fade out bản này rồi fade in bản kia) hoặc tạo lại bản căng thẳng cho khớp BPM."]
    return [synced]


# AudioImporter loadType: 0 Decompress On Load, 1 Compressed In Memory, 2 Streaming.
_UNITY_LOAD = {"music": (2, 0, 1), "ambience": (1, 1, 1), "sfx": (0, 1, 0)}


def unity_import(role: str) -> tuple[int, int, int]:
    """(loadType, preloadAudioData, loadInBackground): long music streams, looping beds stay compressed in RAM,
    short SFX decompress once so PlayOneShot has no decode latency."""
    return _UNITY_LOAD["music" if role.startswith("music") else role if role in _UNITY_LOAD else "sfx"]


def unity_audio_meta(g: str, load_type: int, preload: int, background: int, mono: bool = False) -> str:
    return (f"fileFormatVersion: 2\nguid: {g}\nAudioImporter:\n  externalObjects: {{}}\n  serializedVersion: 8\n"
            f"  defaultSettings:\n    serializedVersion: 2\n    loadType: {load_type}\n    sampleRateSetting: 0\n"
            "    sampleRateOverride: 44100\n    compressionFormat: 1\n    quality: 1\n    conversionMode: 0\n"
            f"    preloadAudioData: {preload}\n  platformSettingOverrides: {{}}\n  forceToMono: {int(mono)}\n"
            f"  normalize: 0\n  loadInBackground: {background}\n  ambisonic: 0\n  3D: 0\n  userData: \n"
            "  assetBundleName: \n  assetBundleVariant: \n")


_STREAM_LOOP = "loop=true\nloop_offset=0.0\nbpm=0.0\nbeat_count=0\nbar_beats=4"
_GODOT_LOOP = {
    ".ogg": ('importer="oggvorbisstr"\ntype="AudioStreamOggVorbis"', _STREAM_LOOP),
    ".mp3": ('importer="mp3"\ntype="AudioStreamMP3"', _STREAM_LOOP),
    ".wav": ('importer="wav"\ntype="AudioStreamWAV"', "edit/loop_mode=2\nedit/loop_begin=0\nedit/loop_end=-1"),
}


def godot_loop_import(name: str) -> bytes | None:
    """A .import without uid/dest: Godot fills those in on first import and keeps these params (loop on)."""
    spec = _GODOT_LOOP.get(Path(name).suffix.lower())
    return None if spec is None else f"[remap]\n\n{spec[0]}\n\n[params]\n\n{spec[1]}\n".encode()


def unity_files(src: Source) -> tuple[Files, list[str]]:
    from studio_core.export.unity import guid

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
            how = ("Loop bật, Play On Awake bật, Spatial Blend 0 (2D)"
                   if it["role"].startswith("music") else "Loop bật, Play On Awake bật, Spatial Blend 0")
        lines.append(f"  · {name} ({ROLE_VI.get(it['role'], it['role'])}): Volume {_linear(it['volume_db'])} "
                     f"({it['volume_db']} dB); {how}.")
    lines += _music_pair_line(src, items, "  Hai bản nhạc cùng BPM/giọng/độ dài: phát cả hai cùng "
                              "PlayScheduled(dspTime) rồi crossfade Volume khi vào/ra giao tranh.")
    lines.append("  .meta kèm theo đã đặt Load Type: nhạc = Streaming, ambience = Compressed In Memory, hiệu ứng = "
                 "Decompress On Load (Loop là thuộc tính của AudioSource, không nằm trong clip).")
    files: Files = [(rel, doc_json(src))]
    files += [(f"{it['file']}.meta", unity_audio_meta(guid(src.uid, src.asset, it["file"]),
                                                      *unity_import(str(it["role"]))).encode()) for it in items]
    return files, ["\n".join(lines)]


def godot_files(src: Source) -> tuple[Files, list[str]]:
    items = _items(src)
    if not items:
        return [], []
    rel = f"Audio/{src.asset}_soundscape.json"
    lines = [f"- {rel}: soundscape của cảnh (vai trò, file, volume dB gợi ý, loop). Gợi ý AudioStreamPlayer:",
             _bpm_line(src)]
    files: Files = [(rel, doc_json(src))]
    for it in items:
        name = it["file"].removeprefix("Audio/")
        imp = godot_loop_import(name) if it["loop"] else None
        if imp:
            files.append((f"{it['file']}.import", imp))
        loop = "Loop đã bật sẵn trong .import kèm theo" if imp else "bật Loop ở tab Import"
        how = ("autoplay = false, gọi play() khi xảy ra; AudioStreamPlayer2D/3D nếu phát tại vật thể"
               if it["role"] == "sfx" else f"autoplay = true, {loop}")
        bus = "SFX" if it["role"] == "sfx" else "Music" if str(it["role"]).startswith("music") else "Ambience"
        lines.append(f"  · {name} ({ROLE_VI.get(it['role'], it['role'])}): volume_db = {it['volume_db']}, "
                     f"bus = {bus}; {how}.")
    lines += _music_pair_line(src, items, "  Hai bản nhạc cùng BPM/giọng/độ dài: gộp vào AudioStreamSynchronized "
                              "(Godot 4.3+) rồi đổi set_sync_stream_volume() để crossfade khi vào/ra giao tranh.")
    return files, ["\n".join(lines)]
