"""Pack-level merge of per-asset Flutter / Android / iOS exports into one pasteable tree per platform: one pubspec,
one Assets.dart / AssetStudioAssets.kt / AssetStudioAssets.swift, one asset catalog, no clashing file names."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path, PurePosixPath

from studio_core.export import android as an
from studio_core.export import flutter as fl
from studio_core.export import flutter_dart as fd
from studio_core.export import ios as ios_exp
from studio_core.export import ios_swift as sw
from studio_core.export.sheet import ident

MOBILE_ENGINES = ("flutter", "android", "ios")
_NOTE_CUT = "\n\nTài nguyên:"


def _staged(d: Path) -> list[str]:
    return sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file())


def _notes(text: str) -> str:
    lines = text.splitlines()
    i = next((k for k, ln in enumerate(lines) if ln.startswith("- ")), len(lines))
    return "\n".join(lines[i:]).rstrip()


def _sections(notes: dict[str, str]) -> str:
    return "\n\n".join(f"## {f}\n\n{n}" for f, n in notes.items() if n)


class _Tree:
    def __init__(self, root: Path, warnings: list[str]) -> None:
        self.root, self.warnings = root, warnings
        self.rels: set[str] = set()

    def put(self, rel: str, data: Path | bytes, folder: str = "") -> bool:
        dst = self.root / rel
        if rel in self.rels:
            old = dst.read_bytes()
            new = data.read_bytes() if isinstance(data, Path) else data
            if old != new:
                self.warnings.append(f"{folder}: {rel} already comes from another asset, kept the first")
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, Path):
            shutil.copyfile(data, dst)
        else:
            dst.write_bytes(data)
        self.rels.add(rel)
        return True


_FL_IMG = re.compile(r"^assets/images/(?:(?:2\.0x|3\.0x)/)?([^/]+)$")
_FL_UI = re.compile(r"^assets/ui/([^/]+)$")
_FL_SVG = re.compile(r"^assets/svg/([^/]+)$")
_FL_OWN = {"pubspec_assets.yaml", "lib/gen/asset_studio_assets.dart", "lib/gen/asset_studio_flame.dart",
           "lib/gen/asset_studio_ui.dart", "README_FLUTTER.md", "assets/ui/ui_9slice.json"}


class FlutterPack:
    def __init__(self, root: Path, title: str, warnings: list[str], demo: bool = False) -> None:
        self.tree, self.title, self.demo = _Tree(root, warnings), title, demo
        self.img: set[str] = set()
        self.ui: set[str] = set()
        self.svg: set[str] = set()
        self.nine: list[dict] = []
        self.packages = ["flame"]
        self.notes: dict[str, str] = {}
        self.flags = {"anim": False, "nine": False, "parallax": False, "font": False, "tiled": False}

    def add(self, folder: str, d: Path) -> list[str]:
        rels = [r for r in _staged(d) if not r.startswith("demo/")]
        imgs = {m[1] for r in rels if (m := _FL_IMG.match(r))}
        uis = {m[1] for r in rels if (m := _FL_UI.match(r)) and r not in _FL_OWN}
        svgs = {m[1] for r in rels if (m := _FL_SVG.match(r))}
        prefix = f"{ident(folder, 'asset')}_" if imgs & self.img or uis & self.ui or svgs & self.svg else ""
        self.img |= {prefix + n for n in imgs}
        self.ui |= {prefix + n for n in uis}
        self.svg |= {prefix + n for n in svgs}
        got: list[str] = []
        for rel in rels:
            if rel in _FL_OWN:
                continue
            m = _FL_IMG.match(rel) or _FL_UI.match(rel) or _FL_SVG.match(rel)
            out = f"{PurePosixPath(rel).parent.as_posix()}/{prefix}{PurePosixPath(rel).name}" if m and prefix else rel
            if self.tree.put(out, d / rel, folder):
                got.append(out)
            self.flags["anim"] |= rel.endswith("_anim.json")
            self.flags["parallax"] |= rel.endswith("/parallax.json")
            self.flags["font"] |= rel.endswith("_font.json")
            self.flags["tiled"] |= rel.startswith("assets/tiles/") and rel.endswith(".tmx")
        nine = d / "assets/ui/ui_9slice.json"
        if nine.is_file():
            self.nine += [{**e, "name": prefix + e["name"]} for e in json.loads(nine.read_text(encoding="utf-8"))]
            self.flags["nine"] = True
        self.flags["nine"] |= (d / "lib/gen/asset_studio_ui.dart").is_file()
        readme = d / "README_FLUTTER.md"
        if readme.is_file():
            text = readme.read_text(encoding="utf-8")
            self.notes[folder] = _notes(text).replace(fl.NOTE_DEMO, "").strip()
            if m := re.search(r"^2\. Package: (.+)\.$", text, re.M):
                self.packages += [p for p in m[1].split(", ") if p not in self.packages]
        return got

    def finish(self) -> list[str]:
        if not self.tree.rels:
            return []
        put = self.tree.put
        if self.flags["nine"]:
            if self.nine:
                put("assets/ui/ui_9slice.json", json.dumps(self.nine, indent=1).encode())
            entries = [(e["name"], f"assets/ui/{e['name']}.png", (e["l"], e["t"], e["r"], e["b"]), (e["w"], e["h"]))
                       for e in self.nine]
            put("lib/gen/asset_studio_ui.dart", fd.nine_slices_dart(entries).encode())
        if any(self.flags.values()):
            put("lib/gen/asset_studio_flame.dart",
                fd.flame_dart(*(self.flags[k] for k in ("anim", "nine", "parallax", "font", "tiled"))).encode())
        root = self.tree.root
        paths = sorted(p.relative_to(root).as_posix() for d in ("assets", "shaders") if (root / d).is_dir()
                       for p in (root / d).rglob("*") if p.is_file())
        if fd.asset_paths(paths):
            put("pubspec_assets.yaml", fd.pubspec_snippet(paths, self.packages).encode())
            put("lib/gen/asset_studio_assets.dart", fd.assets_dart(paths).encode())
        if self.demo:
            from studio_core.export.flutter_demo import write_demo

            if write_demo(root, self.title):
                self.notes["demo"] = fl.NOTE_DEMO
            else:
                self.tree.warnings.append("flutter demo: no character sprite sheet in the pack, demo/ not written")
        put("README_FLUTTER.md", fl.README.format(asset=self.title, packages=", ".join(self.packages),
                                                  notes=_sections(self.notes)).encode())
        return ["pubspec_assets.yaml", "lib/gen/asset_studio_assets.dart"] if fd.asset_paths(paths) else []


_KT_SECTION = re.compile(r"object (Images|NinePatches|Clips) \{")
_KT_VAL = re.compile(r"@DrawableRes val (\w+): Int = R\.drawable\.(\w+)")
_KT_CLIP = re.compile(r"val (\w+) = AssetStudioClip\(listOf\((.*)\), ([^,]+)f, (true|false)\)$")


def parse_kotlin(text: str) -> tuple[list[str], list[str], list[an.Clip]]:
    images: list[str] = []
    patches: list[str] = []
    clips: list[an.Clip] = []
    section = ""
    for line in text.splitlines():
        s = line.strip()
        if m := _KT_SECTION.match(s):
            section = m[1]
        elif s == "}":
            section = ""
        elif section in ("Images", "NinePatches") and (m := _KT_VAL.match(s)):
            (images if section == "Images" else patches).append(m[1])
        elif section == "Clips" and (m := _KT_CLIP.match(s)):
            clips.append(an.Clip(m[1], re.findall(r"R\.drawable\.(\w+)", m[2]), float(m[3]), m[4] == "true"))
    return images, patches, clips


class AndroidPack:
    def __init__(self, root: Path, title: str, package: str, warnings: list[str]) -> None:
        self.tree, self.title, self.package = _Tree(root, warnings), title, package
        self.names: set[str] = set()
        self.images: list[str] = []
        self.patches: list[str] = []
        self.clips: list[an.Clip] = []
        self.notes: dict[str, str] = {}

    def _unique(self, name: str) -> str:
        out, n = name, 2
        while out in self.names:
            out, n = f"{name}_{n}", n + 1
        self.names.add(out)
        return out

    def add(self, folder: str, d: Path) -> list[str]:
        kt = d / an.KOTLIN_FILE
        images, patches, clips = parse_kotlin(kt.read_text(encoding="utf-8")) if kt.is_file() else ([], [], [])
        prefix = re.sub(r"[^a-z0-9_]+", "_", ident(folder, "asset").lower()).strip("_") + "_"
        if prefix[0].isdigit():  # aapt2 rejects resource names that start with a digit
            prefix = "asset_" + prefix
        mapping: dict[str, str] = {}
        for n in [*images, *patches, *(c.name for c in clips), *(f for c in clips for f in c.frames)]:
            if n not in mapping:
                mapping[n] = self._unique(prefix + n)
        self.images += [mapping[n] for n in images]
        self.patches += [mapping[n] for n in patches]
        self.clips += [an.Clip(mapping[c.name], [mapping[f] for f in c.frames], c.fps, c.loop) for c in clips]
        got: list[str] = []
        for rel in _staged(d):
            if rel in (an.KOTLIN_FILE, "README_ANDROID.md"):
                continue
            parts = rel.split("/")
            rel_out = rel
            if len(parts) == 3 and parts[0] == "res" and parts[1].startswith("drawable"):
                stem = parts[2].split(".")[0]
                if stem in mapping:
                    rel_out = f"res/{parts[1]}/{mapping[stem]}{parts[2][len(stem):]}"
            if self.tree.put(rel_out, d / rel, folder):
                got.append(rel_out)
        text = (d / "README_ANDROID.md").read_text(encoding="utf-8")
        body = text.split("\n\n", 1)[1].split(_NOTE_CUT)[0]
        listing = [f"image {mapping[n]}" for n in images] + [f"9-slice {mapping[n]}" for n in patches]
        listing += [f"anim {mapping[c.name]} ({len(c.frames)} khung, {c.fps:g} fps, {'lặp' if c.loop else 'một lần'})"
                    for c in clips]
        listing += re.findall(r"^  \* (fx .+)$", text.split(_NOTE_CUT, 1)[-1], re.M)
        self.notes[folder] = body + "\n\nTài nguyên:\n" + "\n".join(f"  * {x}" for x in listing)
        return got

    def finish(self) -> list[str]:
        if not self.tree.rels:
            return []
        has_res = bool(self.images or self.patches or self.clips)
        if has_res:
            self.tree.put(an.KOTLIN_FILE, an.kotlin_source(self.package, self.images, self.patches, self.clips).encode())
        head = an.README_HEAD.format(asset=self.title, pkg=self.package, pkgpath=self.package.replace(".", "/"))
        head += ("Trong gói, mọi tên tài nguyên có tiền tố theo thư mục asset (<asset>_<tên>) để các asset không "
                 "đè nhau; AssetStudioAssets.kt đã gộp cho cả gói.\n\n")
        self.tree.put("README_ANDROID.md", (head + _sections(self.notes) + "\n").encode())
        return [an.KOTLIN_FILE] if has_res else []


_IOS_SET = (".imageset", ".spriteatlas")


class IosPack:
    def __init__(self, root: Path, title: str, warnings: list[str]) -> None:
        self.tree, self.title = _Tree(root, warnings), title
        self.taken: set[str] = set()
        self.values: list[str] = []
        self.nine: list[tuple[str, tuple[int, int, int, int]]] = []
        self.parallax: list[tuple[str, list[tuple[str, float]]]] = []
        self.anim = False
        self.notes: dict[str, str] = {}

    def _name(self, folder: str, nm: str) -> str:
        out, n = nm, 2
        base = f"{ident(folder, 'asset')}_{nm}"
        while out.lower() in self.taken:
            out = base if n == 2 else f"{base}_{n}"
            n += 1
        self.taken.add(out.lower())
        return out

    def add(self, folder: str, d: Path) -> list[str]:
        rels = _staged(d)
        cat = ios_exp.CATALOG + "/"
        sets: list[str] = []
        for r in rels:
            if r.startswith(cat):
                top = r[len(cat):].split("/")[0]
                if top.endswith(_IOS_SET) and top not in sets:
                    sets.append(top)
        mapping = {s: self._name(folder, s[:s.rindex(".")]) + s[s.rindex("."):] for s in sets}
        short = {k[:k.rindex(".")]: v[:v.rindex(".")] for k, v in mapping.items() if k.endswith(".imageset")}
        for s in sets:
            if s.endswith(".imageset"):
                cj = d / cat / s / "Contents.json"
                for e in json.loads(cj.read_text(encoding="utf-8")).get("images", []):
                    rs = e.get("resizing")
                    if rs and e.get("scale") == "1x":
                        c = rs["cap-insets"]
                        self.nine.append((short[s[:-9]], (c["left"], c["top"], c["right"], c["bottom"])))
        got: list[str] = []
        for rel in rels:
            if rel in ("Sources/AssetStudioKit.swift", "Sources/AssetStudioAssets.swift", "README_IOS.md",
                       "Info_plist_fonts.xml"):
                continue
            data: Path | bytes = d / rel
            out = rel
            if rel.startswith(cat):
                parts = rel[len(cat):].split("/")
                old = parts[0]
                if old in mapping and mapping[old] != old:
                    new = mapping[old]
                    o, n = old[:old.rindex(".")], new[:new.rindex(".")]
                    parts[0] = new
                    if len(parts) > 1 and parts[1].startswith(f"{o}@"):
                        parts[1] = n + parts[1][len(o):]
                    if parts[-1] == "Contents.json" and old.endswith(".imageset"):
                        data = (d / rel).read_bytes().replace(f'"{o}@'.encode(), f'"{n}@'.encode())
                    out = cat + "/".join(parts)
            m = re.match(r"^Resources/(.+)_parallax\.json$", rel)
            if m:
                doc = json.loads((d / rel).read_text(encoding="utf-8"))
                for layer in doc["layers"]:
                    layer["image"] = short.get(layer["image"], layer["image"])
                data = json.dumps(doc, indent=2).encode()
                self.parallax.append((m[1], [(lay["image"], float(lay["scroll"])) for lay in doc["layers"]]))
            if self.tree.put(out, data, folder):
                got.append(out)
            self.anim |= out.endswith(".spriteatlas/Contents.json")
        values = re.findall(r'static let \w+ = "([^"]*)"', (d / "Sources/AssetStudioAssets.swift").read_text("utf-8")) \
            if (d / "Sources/AssetStudioAssets.swift").is_file() else []
        self.values += [short.get(v, v) for v in values]
        readme = d / "README_IOS.md"
        if readme.is_file():
            self.notes[folder] = _notes(readme.read_text(encoding="utf-8"))
        return got

    def finish(self) -> list[str]:
        if not self.tree.rels:
            return []
        put = self.tree.put
        out: list[str] = []
        if self.anim or self.nine or self.parallax:
            kit = sw.kit_swift(sw.nine_slices_swift(self.nine) if self.nine else "", self.anim,
                               sw.parallax_swift(self.parallax) if self.parallax else "")
            put("Sources/AssetStudioKit.swift", kit.encode())
            out.append("Sources/AssetStudioKit.swift")
        if self.values:
            put("Sources/AssetStudioAssets.swift", sw.assets_swift(self.values).encode())
            out.append("Sources/AssetStudioAssets.swift")
        ttf = sorted(r.split("/", 1)[1] for r in self.tree.rels
                     if r.startswith("Fonts/") and r.lower().endswith(ios_exp.FONT_EXT))
        if ttf:
            put("Info_plist_fonts.xml", sw.plist_fonts_snippet(ttf).encode())
        put("README_IOS.md", ios_exp.README.format(asset=self.title, notes=_sections(self.notes)).encode())
        return out
