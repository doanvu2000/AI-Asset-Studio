from __future__ import annotations

import pytest

from studio_core import hardware as hw
from studio_core.bootstrap import plan
from studio_core.hardware import Env, GpuInfo, Hardware, Needs, PackState


def machine(vram_gb: float | None, ram_gb: float = 64, disk_gb: float = 500, driver: float = 600) -> Hardware:
    gpu = None
    if vram_gb is not None:
        problem = plan.Gpu("Test GPU", driver, vram_gb).problem()
        gpu = GpuInfo("Test GPU", driver, int(vram_gb * 1024), int(vram_gb * 1024), problem is None, problem)
    return Hardware(gpu, int(ram_gb * 1024), int(ram_gb * 512), 16, int(disk_gb * 1024), hw.tier_of(gpu, int(ram_gb * 1024)))


def env(h: Hardware, installed: bool = True, **tools: bool) -> Env:
    packs = {p["id"]: PackState(installed, 1000, 0 if installed else 20_000) for p in plan.manifest()["packs"]}
    return Env(h, packs, tools.get("comfy", True), tools.get("blender", True), tools.get("claude", True))


GPU_BASE = Needs({"base"}, {"gpu"})


def test_no_gpu_comfy_recipe_red() -> None:
    f = hw.fit_needs(GPU_BASE, env(machine(None)))
    assert f["level"] == "red" and "NVIDIA" in f["reasons"][0]
    assert f["needs"]["lanes"] == ["gpu"] and f["needs"]["packs"] == ["base"]


def test_gpu_lane_without_pack_needs_gpu() -> None:
    assert hw.fit_needs(Needs(set(), {"gpu"}), env(machine(None)))["level"] == "red"
    assert hw.fit_needs(Needs(set(), {"gpu"}), env(machine(12)))["level"] == "green"


def test_cpu_pack_green_without_gpu() -> None:
    f = hw.fit_needs(Needs({"tts"}, set()), env(machine(None)))
    assert f["level"] == "green" and f["reasons"] == []


def test_low_vram_enough_ram_yellow() -> None:
    f = hw.fit_needs(GPU_BASE, env(machine(8, ram_gb=32)))
    assert f["level"] == "yellow" and "VRAM" in f["reasons"][0] and "offload" in f["reasons"][0]


def test_low_vram_low_ram_red() -> None:
    f = hw.fit_needs(GPU_BASE, env(machine(4, ram_gb=8)))
    assert f["level"] == "red" and "RAM" in f["reasons"][0]


def test_12gb_machine_base_green_and_big_packs_yellow() -> None:
    e = env(machine(11.94, ram_gb=63.8))
    assert hw.fit_needs(Needs({"base", "edit", "angles", "mesh"}, {"gpu"}), e)["level"] == "green"
    f = hw.fit_needs(Needs({"vfx"}, {"gpu"}), e)
    assert f["level"] == "yellow" and "16 GB VRAM" in f["reasons"][0]


def test_32gb_ram_cannot_offload_64gb_pack() -> None:
    assert hw.fit_needs(Needs({"skybox"}, {"gpu"}), env(machine(12, ram_gb=32)))["level"] == "red"


def test_old_driver_red() -> None:
    f = hw.fit_needs(GPU_BASE, env(machine(12, driver=550)))
    assert f["level"] == "red" and "Driver" in f["reasons"][0]


def test_missing_pack_yellow_and_listed() -> None:
    e = env(machine(12))
    e.packs["edit"] = PackState(False, 4000, 4000)
    f = hw.fit_needs(Needs({"base", "edit"}, {"gpu"}), e)
    assert f["level"] == "yellow" and f["missing_packs"] == ["edit"]


def test_missing_pack_disk_too_small_red() -> None:
    e = env(machine(12, disk_gb=5), installed=False)
    f = hw.fit_needs(Needs({"angles"}, {"gpu"}), e)
    assert f["level"] == "red" and any("không đủ" in r for r in f["reasons"])


def test_tools_missing_yellow() -> None:
    assert hw.fit_needs(Needs(set(), {"blender"}), env(machine(12), blender=False))["level"] == "yellow"
    assert hw.fit_needs(Needs(set(), {"cloud"}), env(machine(None), claude=False))["level"] == "yellow"
    assert hw.fit_needs(Needs(set(), {"cloud"}), env(machine(None)))["level"] == "green"
    assert hw.fit_needs(GPU_BASE, env(machine(12), comfy=False))["level"] == "yellow"


def test_flow_merges_worst_level_and_empty_flow_green() -> None:
    e = env(machine(12, ram_gb=32))
    recipes = {"a": GPU_BASE, "b": Needs({"skybox"}, {"gpu"}), "c": Needs({"tts"}, set())}
    out = hw.fit_all(e, recipes, {"f": ["a", "b", "c"], "ok": ["a", "c"], "empty": [], "ghost": ["zzz"]})
    assert out["recipes"]["b"]["level"] == "red"
    f = out["flows"]
    assert f["f"]["level"] == "red" and f["f"]["needs"]["packs"] == ["base", "skybox", "tts"]
    assert f["ok"]["level"] == "green"
    assert f["empty"]["level"] == "green" and f["ghost"]["level"] == "green"


def test_recommend_shape() -> None:
    out = hw.fit_all(env(machine(12, ram_gb=32, disk_gb=30), installed=False), {}, {})
    rec = out["recommend"]
    by = {p["id"]: p for p in rec["packs"]}
    assert set(by) == {p["id"] for p in plan.manifest()["packs"]}
    assert by["base"]["level"] == "green" and by["base"]["installed"] is False and by["base"]["size_mb"] == 1000
    assert by["skybox"]["level"] == "red"
    assert rec["lane_limits"] == {"cpu": 4} and rec["notes"]
    assert out["hardware"]["gpu"]["ok"] is True and out["hardware"]["tier"] == "mid"


def test_tiers() -> None:
    assert machine(None).tier == "low"
    assert machine(6).tier == "low"
    assert machine(12).tier == "mid"
    assert machine(24, ram_gb=64).tier == "high"
    assert machine(24, ram_gb=16).tier == "mid"


def test_every_pack_declares_hw() -> None:
    for p in plan.manifest()["packs"]:
        h = p["hw"]
        assert set(h) == {"min_vram_gb", "rec_vram_gb", "min_ram_gb"} and h["min_vram_gb"] <= h["rec_vram_gb"]
    cpu = {p["id"] for p in plan.manifest()["packs"] if p["hw"]["rec_vram_gb"] == 0}
    assert {"similar", "tts"} <= cpu


def test_parse_meminfo_fallback() -> None:
    text = "MemTotal:       32768000 kB\nMemFree:  1000 kB\nMemAvailable:   16384000 kB\n"
    assert hw.parse_meminfo(text) == (32000, 16000)
    assert hw.parse_meminfo("nothing") is None


def test_read_ram_falls_back_to_proc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hw.sys, "platform", "linux")
    monkeypatch.setattr(hw.Path, "read_text", lambda self, **k: "MemTotal: 2048 kB\nMemAvailable: 1024 kB\n")
    assert hw.read_ram() == (2, 1)
    monkeypatch.setattr(hw.Path, "read_text", lambda self, **k: (_ for _ in ()).throw(OSError()))
    assert hw.read_ram() == (0, 0)


def test_detect_gpu_info_picks_largest_and_handles_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hw, "_query_gpu", lambda: [["A", "580.1", "8192", "4000"], ["B", "610.8", "12227", "1472"]])
    g = hw.detect_gpu_info()
    assert g and g.name == "B" and g.vram_total_mb == 12227 and g.vram_free_mb == 1472 and g.ok
    monkeypatch.setattr(hw, "_query_gpu", lambda: [])
    assert hw.detect_gpu_info() is None
    monkeypatch.setattr(hw, "_query_gpu", lambda: [["Old", "470.1", "8192", "8000"]])
    g = hw.detect_gpu_info()
    assert g and not g.ok and "Driver" in (g.problem or "")


def test_probe_caches(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    calls: list[int] = []
    monkeypatch.setattr(hw, "_query_gpu", lambda: calls.append(1) or [["A", "600.0", "12000", "9000"]])
    monkeypatch.setattr(hw, "read_ram", lambda: (65536, 30000))
    monkeypatch.setattr(hw, "_cache", None)
    a = hw.probe(tmp_path)
    b = hw.probe(tmp_path)
    assert len(calls) == 1 and a.tier == "mid" and b.disk_free_mb and b.ram_total_mb == 65536
    hw.probe(tmp_path, force=True)
    assert len(calls) == 2
