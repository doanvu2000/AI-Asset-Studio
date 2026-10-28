"""Compiles every sprite shader preset in the real engines: Godot (import + render each ShaderMaterial on a sprite) and
Unity batchmode (import the .shader files into a URP project, read ShaderUtil messages).

uv run python scripts/verify_shaders.py [--godot EXE] [--unity EXE --unity-project DIR] [--work DIR]
The Unity project must have com.unity.render-pipelines.universal and must not be open in the Editor."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from studio_core.export import godot, preview, unity
from studio_core.export.shaders import PRESETS, apply, resolve
from studio_core.export.source import Source

GODOT_CHECK = r"""extends SceneTree

func _init() -> void:
	var dir: String = OS.get_cmdline_user_args()[0]
	var fails := 0
	for f in DirAccess.get_files_at(dir + "/Shaders"):
		if not f.ends_with(".tres"):
			continue
		var mat = load(dir + "/Shaders/" + f)
		if not (mat is ShaderMaterial) or mat.shader == null:
			printerr("PREVIEW_FAIL not a ShaderMaterial ", f)
			fails += 1
			continue
		var uniforms: Array = mat.shader.get_shader_uniform_list()
		if uniforms.is_empty():
			printerr("PREVIEW_FAIL shader did not compile (no uniforms) ", f)
			fails += 1
			continue
		var s := Sprite2D.new()
		s.texture = load(dir + "/" + dir.get_file() + ".png")
		s.material = mat
		s.position = Vector2(320, 320)
		get_root().add_child(s)
		print("SHADER_OK ", f, " uniforms=", uniforms.size())
	var scene = load(dir + "/" + dir.get_file() + ".tscn")
	if scene != null:
		var node = scene.instantiate()
		get_root().add_child(node)
		print("SCENE_MATERIAL ", node.material.shader.resource_path if node.material else "none")
	for i in 6:
		await process_frame
	quit(1 if fails else 0)
"""

UNITY_CHECK = r"""using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

public static class AssetStudioShaderCheck
{
    public static void Run()
    {
        var dir = System.Environment.GetEnvironmentVariable("ASSET_STUDIO_SHADER_DIR");
        var report = System.Environment.GetEnvironmentVariable("ASSET_STUDIO_SHADER_REPORT");
        AssetDatabase.ImportAsset(dir, ImportAssetOptions.ImportRecursive | ImportAssetOptions.ForceUpdate);
        var lines = new List<string>();
        int errors = 0;
        foreach (var guid in AssetDatabase.FindAssets("t:Shader", new[] { dir }))
        {
            var path = AssetDatabase.GUIDToAssetPath(guid);
            var shader = AssetDatabase.LoadAssetAtPath<Shader>(path);
            var msgs = ShaderUtil.GetShaderMessages(shader);
            bool bad = shader == null || ShaderUtil.ShaderHasError(shader);
            foreach (var m in msgs)
            {
                if (m.severity == UnityEditor.Rendering.ShaderCompilerMessageSeverity.Error) bad = true;
                lines.Add(path + ": " + m.severity + " " + m.message + " (line " + m.line + ")");
            }
            if (bad) errors++;
            lines.Add((bad ? "SHADER_FAIL " : "SHADER_OK ") + path + " " + (shader ? shader.name : "null") + " passes=" + (shader ? shader.passCount : 0));
        }
        foreach (var guid in AssetDatabase.FindAssets("t:Material", new[] { dir }))
        {
            var path = AssetDatabase.GUIDToAssetPath(guid);
            var mat = AssetDatabase.LoadAssetAtPath<Material>(path);
            bool ok = mat != null && mat.shader != null && mat.shader.name.StartsWith("AssetStudio/");
            if (ok)
            {
                for (int p = 0; p < mat.passCount; p++) ShaderUtil.CompilePass(mat, p, true);
                foreach (var m in ShaderUtil.GetShaderMessages(mat.shader))
                    if (m.severity == UnityEditor.Rendering.ShaderCompilerMessageSeverity.Error)
                    {
                        ok = false;
                        lines.Add(path + ": compile " + m.message + " (line " + m.line + ")");
                    }
                ok &= mat.shader.isSupported;
            }
            if (!ok) errors++;
            lines.Add((ok ? "MAT_OK " : "MAT_FAIL ") + path + " -> " + (mat && mat.shader ? mat.shader.name : "null"));
        }
        foreach (var guid in AssetDatabase.FindAssets("t:Prefab", new[] { dir }))
        {
            var path = AssetDatabase.GUIDToAssetPath(guid);
            var go = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            var sr = go ? go.GetComponent<SpriteRenderer>() : null;
            lines.Add("PREFAB " + path + " material=" + (sr && sr.sharedMaterial ? sr.sharedMaterial.name : "null"));
        }
        File.WriteAllLines(report, lines);
        EditorApplication.Exit(errors == 0 ? 0 : 1);
    }
}
"""


def sheet(path: Path, w: int = 48, h: int = 64, cols: int = 4) -> dict:
    img = Image.new("RGBA", (w * cols, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i in range(cols):
        d.ellipse((i * w + 8, 6, i * w + w - 8, h - 4), fill=(60 + 40 * i, 140, 220, 255), outline=(20, 30, 40, 255))
    img.save(path)
    return {"frame_w": w, "frame_h": h, "cols": cols, "rows": 1, "count": cols, "order": list(range(cols)),
            "width": w * cols, "height": h, "pivot": [w // 2, h - 4],
            "clips": [{"name": "idle", "row0": 0, "rows": 1, "frames": cols, "fps": 8, "loop": True}]}


def export(engine: str, work: Path, names: list[str], asset: str) -> Path:
    png = work / f"{asset}_src.png"
    src = Source(asset=asset, uid=asset, sheet=png, meta=sheet(png), ppu=64,
                 rarity_colors={"common": "#9aa0a6", "rare": "#3d7bfd", "legendary": "#f5a623"},
                 rarity=[("common", png), ("rare", png), ("legendary", png)])
    files = (godot if engine == "godot" else unity).export(src)
    files = apply(files, engine, src, resolve(names, {"dissolve": {"progress": 0.4}}))
    dst = work / engine / asset
    if dst.exists():
        shutil.rmtree(dst)
    for rel, data in files:
        p = dst / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else Path(data).read_bytes())
    return dst


async def check_godot(exe: Path, work: Path) -> bool:
    project = work / "godot_project"
    shutil.rmtree(project, ignore_errors=True)
    project.mkdir(parents=True)
    (project / "project.godot").write_text('config_version=5\n\n[application]\n\nconfig/name="ShaderCheck"\n',
                                           encoding="utf-8")
    (project / "check.gd").write_text(GODOT_CHECK, encoding="utf-8")
    for asset in ("all", "one"):
        shutil.copytree(work / "godot" / asset, project / asset)
    cancel = asyncio.Event()
    imp = await preview.run_proc([str(exe), "--headless", "--path", str(project), "--import"], project, None,
                                 preview.GODOT_IMPORT_TIMEOUT_S, cancel)
    ok = True
    for driver, method in (("opengl3", "gl_compatibility"), ("vulkan", "forward_plus")):
        for asset in ("all", "one"):
            run = await preview.run_proc([str(exe), "--path", str(project), "--rendering-driver", driver,
                                          "--rendering-method", method, "--audio-driver", "Dummy",
                                          "--resolution", "640x640", "--position", "-32000,-32000",
                                          "--script", "res://check.gd", "--", f"res://{asset}"],
                                         project, None, preview.GODOT_RENDER_TIMEOUT_S, cancel)
            errors, warnings = preview.godot_issues(imp.output + "\n" + run.output)
            shader_errs = [ln for ln in run.output.splitlines() if "SHADER ERROR" in ln or "shader" in ln.lower()
                           and "error" in ln.lower()]
            good = run.code == 0 and not errors and not shader_errs
            ok &= good
            print(f"[godot {method} {asset}] exit={run.code} {'OK' if good else 'FAIL'}")
            for ln in run.output.splitlines():
                if ln.startswith(("SHADER_OK", "SCENE_MATERIAL")):
                    print("   ", ln)
            for e in errors + shader_errs:
                print("    ERR", e)
            for w in warnings:
                print("    WARN", w)
    return ok


async def check_unity(exe: Path, project: Path, work: Path) -> bool:
    if preview.project_locked(project):
        print("[unity] project is open in the Editor — skipped")
        return False
    root = project / "Assets" / "AssetStudioShaderCheck"
    shutil.rmtree(root, ignore_errors=True)
    (root / "Editor").mkdir(parents=True)
    (root / "Editor" / "AssetStudioShaderCheck.cs").write_text(UNITY_CHECK, encoding="utf-8")
    for asset in ("all", "one"):
        shutil.copytree(work / "unity" / asset, root / asset)
    report, log = work / "unity_report.txt", work / "unity.log"
    report.unlink(missing_ok=True)
    env = {**os.environ, "ASSET_STUDIO_SHADER_DIR": "Assets/AssetStudioShaderCheck",
           "ASSET_STUDIO_SHADER_REPORT": str(report)}
    run = await preview.run_proc([str(exe), "-batchmode", "-projectPath", str(project),
                                  "-executeMethod", "AssetStudioShaderCheck.Run", "-logFile", str(log)],
                                 project, env, preview.UNITY_TIMEOUT_S, asyncio.Event())
    text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else run.output
    errs = [ln for ln in text.splitlines() if "Shader error" in ln or "error CS" in ln]
    lines = report.read_text(encoding="utf-8").splitlines() if report.is_file() else []
    for ln in lines:
        print("   ", ln)
    for e in errs:
        print("    ERR", e)
    ok = run.code == 0 and bool(lines) and not errs and not any("FAIL" in ln for ln in lines)
    print(f"[unity] exit={run.code} {'OK' if ok else 'FAIL'}")
    return ok


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--godot", default=r"L:\AI\tools\godot\Godot_v4.7.2-stable_win64_console.exe")
    ap.add_argument("--unity", default="")
    ap.add_argument("--unity-project", default="")
    ap.add_argument("--work", default=str(Path(os.environ.get("TEMP", ".")) / "asset_studio_shader_check"))
    args = ap.parse_args()
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    ok = True
    for engine in ("godot", "unity"):
        export(engine, work, list(PRESETS), "all")
        export(engine, work, ["hit_flash"], "one")
    if args.godot and Path(args.godot).is_file():
        ok &= await check_godot(Path(args.godot), work)
    if args.unity and args.unity_project:
        ok &= await check_unity(Path(args.unity), Path(args.unity_project), work)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
