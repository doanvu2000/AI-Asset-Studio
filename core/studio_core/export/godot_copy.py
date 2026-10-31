from __future__ import annotations

import os
import posixpath
import re
import shutil
from pathlib import Path

_GD_CLASS = re.compile(r"^\s*class_name\s+([A-Za-z_]\w*)", re.M)
_EXT_LINE = re.compile(r"^\[ext_resource\b[^\n]*\]$", re.M)
_PATH_ATTR = re.compile(r'\bpath="([^"]*)"')
_UID_ATTR = re.compile(r'\s+uid="[^"]*"')


def gd_class(p: Path) -> str | None:
    try:
        m = _GD_CLASS.search(p.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return m[1] if m else None


def _res(p: Path, root: Path) -> str:
    return "res://" + p.relative_to(root).as_posix()


def _project_scripts(root: Path, target: Path) -> list[Path]:
    """Godot registers no class from hidden folders (.godot cache) or folders holding a .gdignore."""
    out = []
    for d, dirs, files in os.walk(root):
        here = Path(d)
        if here.is_relative_to(target) or (here != root and ".gdignore" in files):
            dirs[:] = []
            continue
        dirs[:] = [n for n in dirs if not n.startswith(".")]
        out += [here / f for f in files if f.endswith(".gd")]
    return sorted(out, key=lambda q: q.as_posix())


def _repoint(text: str, rel_dir: str, target_res_dir: str, moved: dict[str, str]) -> str:
    """ext_resource paths are either res:// or relative to the resource's own folder; both forms are matched."""
    def line(m: re.Match[str]) -> str:
        s = m[0]
        pm = _PATH_ATTR.search(s)
        if not pm:
            return s
        p = pm[1]
        key = p if p.startswith("res://") else "res://" + posixpath.normpath(posixpath.join(target_res_dir, rel_dir, p))
        if (new := moved.get(key)) is None:
            return s
        # The reused script has its own uid; a stale uid would win over the path and point back at nothing.
        return _UID_ATTR.sub("", s[:pm.start()] + f'path="{new}"' + s[pm.end():])

    return _EXT_LINE.sub(line, text)


def copy_godot_export(src: Path, target: Path, root: Path) -> list[str]:
    """Copies an export folder into a Godot project. class_name is global, so a script whose file name and
    class_name already exist elsewhere in the project (npc_data.gd / NpcData from another NPC) is refreshed in place
    and this export's .tres/.tscn point at it instead of a second copy ("hides a global script class").
    Returns the reused res:// paths."""
    target, root = target.resolve(), root.resolve()
    own = {p: c for p in src.rglob("*.gd") if (c := gd_class(p))}
    have: dict[tuple[str, str], Path] = {}
    if own:
        for p in _project_scripts(root, target):
            if (c := gd_class(p)) and (p.name, c) not in have:
                have[(p.name, c)] = p
    skip: dict[Path, Path] = {}
    for p, c in own.items():
        if (dst := have.get((p.name, c))) is not None:
            skip[p] = dst
    target_res = _res(target, root)[len("res://"):]
    moved = {"res://" + posixpath.normpath(posixpath.join(target_res, p.relative_to(src).as_posix())): _res(d, root)
             for p, d in skip.items()}
    for p in sorted(src.rglob("*"), key=lambda q: q.as_posix()):
        if not p.is_file():
            continue
        rel = p.relative_to(src)
        to = target / rel
        if (dst := skip.get(p)) is not None:
            shutil.copyfile(p, dst)
            to.unlink(missing_ok=True)
            to.with_name(to.name + ".uid").unlink(missing_ok=True)
            continue
        if p.name.endswith(".gd.uid") and p.with_name(p.name[:-4]) in skip:
            continue
        to.parent.mkdir(parents=True, exist_ok=True)
        if moved and p.suffix.lower() in (".tres", ".tscn"):
            text = p.read_text(encoding="utf-8")
            to.write_text(_repoint(text, rel.parent.as_posix(), target_res, moved), encoding="utf-8", newline="")
            shutil.copystat(p, to)
        else:
            shutil.copy2(p, to)
    return sorted(_res(d, root) for d in set(skip.values()))
