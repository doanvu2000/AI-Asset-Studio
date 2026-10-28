"""`op.animatic`: storyboard frames + voiced lines (+ optional music bed / SFX) → animatic.mp4 (H.264 + AAC). Shot
durations stretch to fit their voice line, each still gets a Ken Burns move from its camera, shots crossfade, subtitles
are burned as PNG overlays (no libass/fontconfig needed) and written as .srt; plus a storyboard sheet (PNG + PDF)."""
from __future__ import annotations

import asyncio
import json
import math
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from ..config import _default_ffmpeg_exe
from ..contracts import BlobRef, Outputs, StepContext, StepError, StepResult
from .audio import _ffmpeg
from .executors import OpExecutor, _load, _put_png
from .keyart import fit_cover, grid_size
from .localize import load_font, pick_font
from .rig import _put_json

ASPECTS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1), "4:3": (4, 3)}
FRAMINGS = ("wide", "medium", "close")
CAMERAS = ("static", "push_in", "pull_out", "pan_left", "pan_right")
FPS = 24
XFADE = 0.5
LEAD = 0.25
TAIL = 0.45
END_FADE = 0.6
MIN_SHOT = 1.5
KB_AMOUNT = 0.12
RATE = 48000
SHOT_NAME = re.compile(r"shot_(\d+)")


def video_size(aspect: str, short: int = 720) -> tuple[int, int]:
    aw, ah = ASPECTS.get(aspect, ASPECTS["16:9"])
    if aw >= ah:
        return round(short * aw / ah / 2) * 2, short
    return short, round(short * ah / aw / 2) * 2


def gen_size(aspect: str) -> tuple[int, int]:
    aw, ah = ASPECTS.get(aspect, ASPECTS["16:9"])
    return grid_size(aw, ah, 1024 * 1024, 16)


def shot_index(name: str | None) -> int | None:
    m = SHOT_NAME.search(name or "")
    return int(m.group(1)) if m else None


def fit_timeline(shots: list[dict[str, Any]], voice: dict[str, float], fps: int = FPS,
                 xfade: float = XFADE) -> tuple[list[dict[str, Any]], float]:
    """Each shot lasts at least its planned duration and long enough for its line to start after the incoming
    crossfade and end before the outgoing one. Durations snap to whole frames so xfade offsets stay exact."""
    out: list[dict[str, Any]] = []
    t = 0.0
    n = len(shots)
    for i, s in enumerate(shots):
        last = i == n - 1
        lead = (xfade if i else 0.0) + LEAD
        v = float(voice.get(s["id"]) or 0.0)
        need = lead + v + TAIL + (END_FADE if last else xfade) if v > 0 else 0.0
        d = max(float(s.get("duration") or 0), need, MIN_SHOT, 2 * xfade + 0.25)
        d = math.ceil(d * fps - 1e-6) / fps
        entry = {"id": s["id"], "start": round(t, 3), "duration": round(d, 4), "frames": round(d * fps),
                 "planned": float(s.get("duration") or 0), "extended": d > float(s.get("duration") or 0) + 1 / fps}
        if v > 0:
            entry.update(line_start=round(t + lead, 3), line_end=round(t + lead + v, 3), voice_seconds=round(v, 3))
        out.append(entry)
        t = t + d if last else t + d - xfade
    return out, round(t, 3)


def ken_burns(camera: str, frames: int, size: tuple[int, int], fps: int = FPS, amount: float = KB_AMOUNT) -> str:
    w, h = size
    p = f"on/{max(frames - 1, 1)}"
    x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if camera == "push_in":
        z = f"1+{amount}*{p}"
    elif camera == "pull_out":
        z = f"{1 + amount}-{amount}*{p}"
    elif camera in ("pan_left", "pan_right"):
        z = f"{1 + amount}"
        x = f"(iw-iw/zoom)*{p}" if camera == "pan_right" else f"(iw-iw/zoom)*(1-{p})"
    else:
        z = "1"
    return f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={fps}"


def _pitch_chain(p: float) -> str:
    if abs(p - 1.0) < 1e-3:
        return ""
    # asetrate shifts pitch and tempo together; atempo puts the tempo back so the line keeps its measured length.
    return f"asetrate={round(RATE * p)},aresample={RATE},atempo={1 / p:.4f},"


def build_ffmpeg_args(timeline: list[dict[str, Any]], total: float, frames: list[Path], cameras: list[str],
                      size: tuple[int, int], out: Path, *, fps: int = FPS, xfade: float = XFADE,
                      subs: list[tuple[Path, int, int, float, float]] = (),
                      voices: list[tuple[Path, float, float]] = (), music: Path | None = None,
                      music_db: float = -20.0, sfx: list[tuple[Path, float]] = (), sfx_db: float = -8.0
                      ) -> list[str]:
    """subs: (png, x, y, start, end); voices: (wav, start, pitch); sfx: (clip, start). Returns ffmpeg args."""
    args: list[str] = []
    fg: list[str] = []
    for f in frames:
        args += ["-i", str(f)]
    for i, (tl, cam) in enumerate(zip(timeline, cameras)):
        fg.append(f"[{i}:v]{ken_burns(cam, tl['frames'], size, fps)},fps={fps},format=yuv420p,setsar=1,"
                  f"settb=AVTB[v{i}]")
    cur = "v0"
    for i in range(1, len(timeline)):
        fg.append(f"[{cur}][v{i}]xfade=transition=fade:duration={xfade}:offset={timeline[i]['start']:.4f}[x{i}]")
        cur = f"x{i}"
    fg.append(f"[{cur}]fade=t=in:st=0:d=0.3,fade=t=out:st={max(total - END_FADE, 0):.3f}:d={END_FADE}[vb]")
    cur = "vb"
    idx = len(frames)
    for k, (png, x, y, a, b) in enumerate(subs):
        args += ["-i", str(png)]
        fg.append(f"[{cur}][{idx}:v]overlay={x}:{y}:enable='between(t,{a:.3f},{b:.3f})'[s{k}]")
        cur, idx = f"s{k}", idx + 1
    fg.append(f"[{cur}]null[vout]")
    fg.append(f"anullsrc=r={RATE}:cl=stereo,atrim=duration={total:.3f}[abase]")
    mix = ["abase"]
    stereo = f"aresample={RATE},aformat=sample_fmts=fltp:channel_layouts=stereo"
    for k, (wav, start, pitch) in enumerate(voices):
        args += ["-i", str(wav)]
        ms = round(start * 1000)
        fg.append(f"[{idx}:a]aresample={RATE},{_pitch_chain(pitch)}{stereo},adelay={ms}:all=1[av{k}]")
        mix.append(f"av{k}")
        idx += 1
    if music is not None:
        args += ["-stream_loop", "-1", "-i", str(music)]
        fade_st = max(total - 2.5, 0)
        fg.append(f"[{idx}:a]{stereo},atrim=duration={total:.3f},volume={music_db}dB,afade=t=in:d=1.5,"
                  f"afade=t=out:st={fade_st:.3f}:d=2.5[am]")
        mix.append("am")
        idx += 1
    for k, (clip, start) in enumerate(sfx):
        args += ["-i", str(clip)]
        fg.append(f"[{idx}:a]{stereo},volume={sfx_db}dB,adelay={round(start * 1000)}:all=1[as{k}]")
        mix.append(f"as{k}")
        idx += 1
    fg.append("".join(f"[{m}]" for m in mix)
              + f"amix=inputs={len(mix)}:duration=first:normalize=0,alimiter=limit=0.95[aout]")
    graph = ";".join(fg)
    return [*args, "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]", "-r", str(fps),
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", str(RATE), "-movflags", "+faststart", "-t", f"{total:.3f}",
            str(out)]


def cues_for(timeline: list[dict[str, Any]], lines: dict[str, str]) -> list[dict[str, Any]]:
    # Lazy: adapters.storyboard imports this module, and the adapters package pulls in storyboard on init.
    from ..adapters import tts_text as tt

    out: list[dict[str, Any]] = []
    for tl in timeline:
        text = lines.get(tl["id"])
        if not text or "line_start" not in tl:
            continue
        for c in tt.subtitle_cues(text, tl["voice_seconds"]):
            out.append({"shot": tl["id"], "start": round(tl["line_start"] + float(c["start"]), 3),
                        "end": round(tl["line_start"] + float(c["end"]) + 0.2, 3), "text": str(c["text"])})
    for a, b in zip(out, out[1:]):
        a["end"] = min(a["end"], b["start"])
    return out


def _stamp(t: float) -> str:
    ms = round(t * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def srt(cues: list[dict[str, Any]]) -> str:
    return "".join(f"{i}\n{_stamp(c['start'])} --> {_stamp(c['end'])}\n{c['text']}\n\n"
                   for i, c in enumerate(cues, 1))


def _font(lang: str, text: str, size: int) -> Any:
    path, _ = pick_font(lang, text, "bold")
    return load_font(path, size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: Any, width: int) -> list[str]:
    lines: list[str] = []
    cur = ""
    for word in text.split():
        nxt = f"{cur} {word}".strip()
        if cur and draw.textlength(nxt, font=font) > width:
            lines.append(cur)
            cur = word
        else:
            cur = nxt
    return [*lines, cur] if cur else lines


def subtitle_image(text: str, size: tuple[int, int], lang: str) -> tuple[Image.Image, int, int]:
    """Cropped RGBA caption and where it goes (bottom centre)."""
    w, h = size
    fs = max(18, round(min(w, h) * 0.05))
    font = _font(lang, text, fs)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    rows = _wrap(probe, text, font, int(w * 0.86))
    stroke = max(2, fs // 12)
    line_h = round(fs * 1.25)
    tw = max(int(probe.textlength(r, font=font)) for r in rows) + 4 * stroke
    img = Image.new("RGBA", (tw, line_h * len(rows) + 2 * stroke), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, r in enumerate(rows):
        rw = d.textlength(r, font=font)
        d.text(((tw - rw) / 2, stroke + i * line_h), r, font=font, fill=(255, 255, 255, 255),
               stroke_width=stroke, stroke_fill=(0, 0, 0, 230))
    return img, (w - tw) // 2, h - img.height - round(h * 0.06)


def contact_sheet(panels: list[tuple[Image.Image, list[str]]], lang: str, cols: int = 3,
                  panel_w: int = 480) -> Image.Image:
    """Storyboard page: each frame with its caption rows under it."""
    if not panels:
        raise StepError("op_no_input", "no shots for the storyboard sheet")
    aspect = panels[0][0].height / panels[0][0].width
    ph = round(panel_w * aspect)
    fs = 15
    sample = " ".join(r for _, rows in panels for r in rows)[:400] or "A"
    font = _font(lang, sample, fs)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    # The long description gets 3 rows at most so the dialogue line under it is never cut off.
    wrapped = [[w for k, r in enumerate(rows)
                for w in (_wrap(probe, r, font, panel_w - 8) or [""])[:3 if k == 1 else 2]] for _, rows in panels]
    text_h = 8 + (fs + 4) * max(len(w) for w in wrapped)
    cols = max(1, min(cols, len(panels)))
    nrows = math.ceil(len(panels) / cols)
    gap = 16
    sheet = Image.new("RGB", (gap + cols * (panel_w + gap), gap + nrows * (ph + text_h + gap)), (246, 245, 242))
    d = ImageDraw.Draw(sheet)
    for i, ((img, _), rows) in enumerate(zip(panels, wrapped)):
        x = gap + (i % cols) * (panel_w + gap)
        y = gap + (i // cols) * (ph + text_h + gap)
        sheet.paste(img.convert("RGB").resize((panel_w, ph), Image.Resampling.LANCZOS), (x, y))
        d.rectangle((x, y, x + panel_w - 1, y + ph - 1), outline=(40, 40, 46), width=1)
        for k, r in enumerate(rows):
            d.text((x + 2, y + ph + 6 + k * (fs + 4)), r, font=font, fill=(30, 30, 36) if k else (150, 60, 30))
    return sheet


def shot_caption(shot: dict[str, Any], tl: dict[str, Any] | None) -> list[str]:
    secs = tl["duration"] if tl else shot.get("duration", 0)
    rows = [f"#{shot.get('index', '?')} · {secs:.1f}s · {shot.get('framing')} · {shot.get('camera')}",
            str(shot.get("description") or "")]
    if shot.get("line"):
        rows.append(f"{shot.get('speaker') or '?'} ({shot.get('expression') or 'neutral'}): {shot['line']}")
    extra = " · ".join(x for x in (f"SFX: {shot['sfx']}" if shot.get("sfx") else "",
                                   f"Music: {shot['music_cue']}" if shot.get("music_cue") else "") if x)
    return [*rows, extra] if extra else rows


def _read(ctx: StepContext, ref: BlobRef) -> bytes:
    return ctx.blobs.path(ref.hash).read_bytes()


class Animatic(OpExecutor):
    kind = "op.animatic"
    version = "1"

    def __init__(self, settings: Any = None):
        self.settings = settings

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        prefs = inputs.get("plan") or []
        if not prefs:
            raise StepError("op_no_input", "input 'plan' (storyboard json) is empty")
        plan = json.loads(await asyncio.to_thread(_read, ctx, prefs[0]))
        frames = {shot_index(r.name): r for r in inputs.get("frames") or [] if shot_index(r.name) is not None}
        shots = [s for s in plan.get("shots") or [] if s.get("index") in frames]
        if not shots:
            raise StepError("op_no_input", "no storyboard frame matches a shot (frames are named shot_XX)")
        dropped = [s["id"] for s in plan.get("shots") or [] if s.get("index") not in frames]
        lang = str(plan.get("language") or "en")
        fps = int(params.get("fps") or FPS)
        xfade = min(max(float(params.get("crossfade", XFADE)), 0.1), 1.5)
        short = 1080 if str(params.get("resolution", "720p")) == "1080p" else 720
        size = video_size(str(plan.get("aspect") or "16:9"), short)

        voice_secs: dict[str, float] = {}
        clips: dict[str, BlobRef] = {}
        for r in inputs.get("tts") or []:
            report = json.loads(await asyncio.to_thread(_read, ctx, r))
            by_stem = {(a.name or "").rsplit(".", 1)[0]: a for a in inputs.get("voice") or []}
            for it in report.get("items") or []:
                if it.get("stem") in by_stem:
                    voice_secs[it["id"]] = float(it.get("seconds") or 0)
                    clips[it["id"]] = by_stem[it["stem"]]
        timeline, total = fit_timeline(shots, voice_secs, fps, xfade)
        tl_by = {tl["id"]: tl for tl in timeline}

        job = Path(ctx.job_dir)
        await asyncio.to_thread(job.mkdir, parents=True, exist_ok=True)
        paths: list[Path] = []
        images: list[Image.Image] = []
        for s in shots:
            img = (await _load(ctx, frames[s["index"]])).convert("RGB")
            images.append(img)
            p = job / f"{s['id']}.png"
            # 2x headroom: zoompan rounds its crop to whole input pixels, which jitters at 1x.
            big = await asyncio.to_thread(fit_cover, img, (size[0] * 2, size[1] * 2))
            await asyncio.to_thread(big.save, p, compress_level=1)
            paths.append(p)
        ctx.progress(0.2, 1.0, "frames")

        cues = cues_for(timeline, {s["id"]: s.get("line") or "" for s in shots})
        burn = str(params.get("burn_subtitles", True)).lower() not in ("false", "0", "no", "off")
        subs: list[tuple[Path, int, int, float, float]] = []
        if burn:
            for k, c in enumerate(cues):
                img, x, y = await asyncio.to_thread(subtitle_image, c["text"], size, lang)
                p = job / f"sub_{k:03d}.png"
                await asyncio.to_thread(img.save, p)
                subs.append((p, x, y, c["start"], c["end"]))

        pitch = {str(c.get("name")): float(c.get("pitch") or 1.0) for c in plan.get("cast") or []}
        voices = []
        for s in shots:
            ref = clips.get(s["id"])
            if ref is not None and "line_start" in tl_by[s["id"]]:
                p = pitch.get(str(s.get("speaker")), 1.0) if lang == "vi" else 1.0
                voices.append((ctx.blobs.path(ref.hash), tl_by[s["id"]]["line_start"], p))
        music, sfx = None, []
        by_idx = {s["index"]: s for s in shots}
        for r in inputs.get("score") or []:
            name = r.name or ""
            if name.startswith("music.") and music is None:
                music = ctx.blobs.path(r.hash)
            elif name.startswith("sfx_") and shot_index(name) in by_idx:
                sfx.append((ctx.blobs.path(r.hash), tl_by[by_idx[shot_index(name)]["id"]]["start"] + 0.1))

        out = job / "animatic.mp4"
        args = build_ffmpeg_args(timeline, total, paths, [s.get("camera", "static") for s in shots], size, out,
                                 fps=fps, xfade=xfade, subs=subs, voices=voices, music=music,
                                 music_db=float(params.get("music_db", -20)), sfx=sfx,
                                 sfx_db=float(params.get("sfx_db", -8)))
        exe = str(getattr(self.settings, "ffmpeg_exe", None) or _default_ffmpeg_exe())
        ctx.progress(0.3, 1.0, "encode")
        await asyncio.to_thread(_ffmpeg, exe, args)
        ctx.progress(0.9, 1.0, "sheet")

        panels = [(img, shot_caption(s, tl_by[s["id"]])) for s, img in zip(shots, images)]
        portrait = size[1] > size[0]
        sheet = await asyncio.to_thread(contact_sheet, panels, lang, 4 if portrait else 3, 300 if portrait else 480)
        pdf = job / "storyboard.pdf"
        await asyncio.to_thread(sheet.save, pdf, "PDF", resolution=150)
        doc = {**plan, "type": "animatic", "fps": fps, "size": list(size), "crossfade": xfade, "seconds": total,
               "dropped_shots": dropped, "subtitles": cues,
               "shots": [{**s, "timing": tl_by[s["id"]], "frame": frames[s["index"]].hash} for s in shots]}
        outputs: Outputs = {
            "video": [await ctx.blobs.put_file(out, name="animatic.mp4", mime="video/mp4")],
            "srt": [await ctx.blobs.put_bytes(srt(cues).encode("utf-8"), name="animatic.srt",
                                              mime="application/x-subrip")],
            "sheet": [await _put_png(ctx, sheet, "storyboard.png")],
            "pdf": [await ctx.blobs.put_file(pdf, name="storyboard.pdf", mime="application/pdf")],
            "json": [await _put_json(ctx, doc, "shots.json")],
        }
        meta = {"seconds": total, "shots": len(shots), "dropped": dropped, "size": list(size), "subtitles": len(cues),
                "voiced": len(voices), "music": music is not None, "sfx": len(sfx),
                "extended": [tl["id"] for tl in timeline if tl["extended"]]}
        if dropped:
            ctx.log(f"shots without a picked frame were cut: {', '.join(dropped)}", "warning")
        return StepResult(outputs=outputs, meta=meta)

