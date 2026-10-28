from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from studio_core.config import _default_ffmpeg_exe
from studio_core.engine.recipe import load_recipes
from studio_core.config import Settings
from studio_core.ops import audio as au


def tone(seconds: float, hz: float = 440.0) -> np.ndarray:
    t = np.arange(int(seconds * au.RATE)) / au.RATE
    s = (0.5 * np.sin(2 * np.pi * hz * t)).astype(np.float32)
    return np.stack([s, s], 1)


def test_trim_fade_normalize() -> None:
    a = np.concatenate([np.zeros((au.RATE, 2), np.float32), tone(0.5), np.zeros((au.RATE // 2, 2), np.float32)])
    t = au.trim_silence(a)
    assert abs(len(t) / au.RATE - 0.52) < 0.02
    f = au.fade(t, 0.01, 0.1)
    assert abs(f[0]).max() == 0 and abs(f[-1]).max() == 0
    n = au.normalize(f, -1.0)
    assert abs(float(np.abs(n).max()) - 10 ** (-1 / 20)) < 1e-4


def test_loop_crossfade_is_seamless() -> None:
    a = tone(4.0, 220.0) * np.linspace(0.2, 1, 4 * au.RATE, dtype=np.float32)[:, None]
    loop = au.make_loop(a, 1.0)
    assert len(loop) == len(a) - au.RATE
    # The loop's last sample hands over to its first one like consecutive source samples do.
    step = np.abs(np.diff(a[:, 0])).max()
    assert abs(loop[-1, 0] - loop[0, 0]) <= 2 * step


@pytest.mark.skipif(shutil.which(_default_ffmpeg_exe()) is None and not Path(_default_ffmpeg_exe()).exists(),
                    reason="ffmpeg not installed")
def test_process_writes_ogg_wav(tmp_path: Path) -> None:
    exe = _default_ffmpeg_exe()
    src = tmp_path / "in.wav"
    au.encode(exe, np.concatenate([np.zeros((au.RATE // 4, 2), np.float32), tone(1.0)]), src)
    res = au.process(exe, src, tmp_path, "hit", {"loop": False, "trim": True, "max_s": 0, "crossfade": 1.0,
                                                 "fade_in": 0.005, "fade_out": 0.05, "peak_db": -1.0,
                                                 "formats": ["ogg", "wav"]})
    assert [f.name for f in res["files"]] == ["hit.ogg", "hit.wav"]
    assert abs(res["seconds"] - 1.02) < 0.05 and res["raw_seconds"] > 1.2
    assert res["wave"].size == (800, 160)
    back = au.decode(exe, tmp_path / "hit.wav")
    assert abs(len(back) / au.RATE - res["seconds"]) < 0.01


def test_audio_recipes_load() -> None:
    recipes = load_recipes(Settings().recipes_dir)
    assert {"audio-sfx", "audio-music", "spine-skin-variant", "concept-2d-style"} <= recipes.keys()
    assert [s.id for s in recipes["concept-2d-style"].steps] == ["ref", "gen", "pick", "rmbg", "fit", "sheet"]
    assert [s.kind for s in recipes["audio-music"].steps] == ["comfy.music_ace_step15", "op.audio_post",
                                                              "gate.approve"]


def test_vfx_flipbook_loop_and_oneshot() -> None:
    from PIL import Image

    from studio_core.ops import vfx

    def frame(r: float, alpha: int = 255) -> Image.Image:
        img = Image.new("RGBA", (128, 96), (0, 0, 0, 0))
        a = np.zeros((96, 128, 4), np.uint8)
        yy, xx = np.mgrid[:96, :128]
        a[((xx - 64) ** 2 + (yy - 48) ** 2) < r * r] = (255, 140, 20, alpha)
        return Image.fromarray(a, "RGBA") if r > 0 else img

    # A pulsing disc whose 6th frame repeats the 0th: the loop is 0..6 (period 6) or a multiple of it.
    radii = [10 + 8 * abs(np.sin(np.pi * i / 6)) for i in range(20)]
    cells, meta = vfx.flipbook([frame(r) for r in radii], "loop", 64)
    assert (meta["loop_end"] - meta["loop_start"]) % 6 == 0 and meta["loop_seam"] < 0.01
    assert cells[0].size == (64, 64)
    burst = [frame(0)] * 3 + [frame(5 + 3 * i) for i in range(10)] + [frame(0)] * 4
    cells, meta = vfx.flipbook(burst, "oneshot", 32, fade=3)
    assert (meta["start"], meta["end"]) == (3, 13) and len(cells) == 10
    assert np.asarray(cells[-1])[..., 3].max() < np.asarray(cells[-4])[..., 3].max()
    # Alpha-VAE edge line: on every frame, it must neither count as content nor widen the crop.
    lined = []
    for f in burst:
        a = np.asarray(f.convert("RGBA")).copy()
        a[0, :, 3] = 140
        lined.append(Image.fromarray(a, "RGBA"))
    _, meta2 = vfx.flipbook(lined, "oneshot", 32, fade=3)
    assert (meta2["start"], meta2["end"]) == (3, 13) and meta2["crop"] == meta["crop"]


def test_audio_post_names_clips_by_label(tmp_path: Path) -> None:
    import asyncio

    from test_ops_executors import FakeCtx

    exe = _default_ffmpeg_exe()
    if shutil.which(exe) is None and not Path(exe).exists():
        pytest.skip("ffmpeg not installed")
    ctx = FakeCtx(tmp_path)
    refs = []
    for i in range(4):
        f = tmp_path / f"c{i}.wav"
        au.encode(exe, tone(0.3, 300 + 100 * i), f)
        refs.append(asyncio.run(ctx.blobs.put_bytes(f.read_bytes(), name=f.name)))
    res = asyncio.run(au.AudioPost().run(ctx, {"labels": "Sword Slash!\ncoin pickup", "per_label": 2, "formats": "ogg"},
                                         {"audio": refs}))
    assert [r.name for r in res.outputs["audio"]] == ["sword_slash_1.ogg", "sword_slash_2.ogg", "coin_pickup_1.ogg",
                                                      "coin_pickup_2.ogg"]
