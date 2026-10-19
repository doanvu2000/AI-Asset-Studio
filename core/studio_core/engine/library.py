"""Built-in recipes (read-only, shipped with core) plus user recipes in <data>/recipes that shadow them by id."""
from __future__ import annotations

import io
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Literal

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from ..contracts import ExecutorRegistry
from .kinds import CATALOG, comfy_entries
from .recipe import _ID, Recipe, RecipeError, load_recipes, parse_recipe

log = logging.getLogger(__name__)

Source = Literal["builtin", "user", "override"]
MAX_RECIPE_BYTES = 512 * 1024


def _signature(d: Path) -> tuple[tuple[str, int, int], ...]:
    if not d.is_dir():
        return ()
    return tuple(sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in d.glob("*.y*ml")))


def dump_yaml(doc: dict[str, Any]) -> str:
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=120, default_flow_style=None)


def to_text(raw: str | dict[str, Any]) -> str:
    return raw if isinstance(raw, str) else dump_yaml(raw)


def _merge(old: Any, new: Any) -> Any:
    """Writes `new` into the round-trip node `old` in place where shapes match, so comments and flow style survive."""
    if isinstance(old, CommentedMap) and isinstance(new, dict):
        for k in [k for k in old if k not in new]:
            del old[k]
        for k, v in new.items():
            old[k] = _merge(old[k], v) if k in old else v
        return old
    if isinstance(old, CommentedSeq) and isinstance(new, list):
        # Steps are matched by id so a reordered or inserted step keeps the comments of the others.
        by_id = {o["id"]: o for o in old if isinstance(o, CommentedMap) and "id" in o}
        # ruamel keys comments between items by index; re-key them to the item they precede.
        notes = {id(old[i]): c for i, c in old.ca.items.items() if isinstance(i, int) and i < len(old)}
        items = []
        for i, v in enumerate(new):
            prev = by_id.get(v.get("id")) if isinstance(v, dict) and by_id else (old[i] if i < len(old) else None)
            items.append(_merge(prev, v) if prev is not None else v)
        old[:] = items
        old.ca.items.clear()
        old.ca.items.update({j: notes[id(x)] for j, x in enumerate(items) if id(x) in notes})
        return old
    return old if isinstance(old, type(new)) and old == new else new


def merge_text(prev_text: str, doc: dict[str, Any]) -> str:
    """Applies an editor doc on top of the recipe's current YAML, keeping its comments and layout."""
    rt = YAML()
    rt.width = 4096
    rt.preserve_quotes = True
    rt.indent(mapping=2, sequence=4, offset=2)
    try:
        tree = rt.load(prev_text)
    except Exception:
        return dump_yaml(doc)
    if not isinstance(tree, CommentedMap):
        return dump_yaml(doc)
    buf = io.StringIO()
    rt.dump(_merge(tree, doc), buf)
    return buf.getvalue()


def issue(message: str, step: str | None = None, level: str = "error", field: str | None = None) -> dict[str, Any]:
    return {"level": level, "step": step, "field": field, "message": message}


def lint(recipe: Recipe, registry: ExecutorRegistry | None, workflows_dir: Path | None = None) -> list[dict[str, Any]]:
    """Checks that need the executor registry: unknown kinds (error) and port names the kind does not declare."""
    out: list[dict[str, Any]] = []
    known = {**CATALOG, **comfy_entries(workflows_dir)}
    outs_of: dict[str, set[str] | None] = {}
    for s in recipe.steps:
        if registry is not None:
            try:
                registry.resolve(s.kind)
            except Exception:
                out.append(issue(f"unknown step kind '{s.kind}'", s.id, field="kind"))
                continue
        spec = known.get(s.kind)
        ports_out = spec.get("out") if spec else None
        outs_of[s.id] = None if ports_out is None or "*" in ports_out else set(ports_out)
        ports_in = spec.get("in") if spec else None
        if ports_in is not None and "*" not in ports_in:
            for name in s.bindings:
                if name not in ports_in:
                    out.append(issue(f"'{s.kind}' has no input port '{name}' (has: {', '.join(ports_in) or '—'})",
                                     s.id, "warning", f"in.{name}"))
    for s in recipe.steps:
        for name, b in ((n, p) for n, bb in s.bindings.items() for p in bb.parts):
            produced = outs_of.get(b.source)
            if b.source != "inputs" and produced is not None and b.name not in produced:
                out.append(issue(f"step '{b.source}' has no output '{b.name}' (has: {', '.join(sorted(produced))})",
                                 s.id, "warning", f"in.{name}"))
    for k, b in recipe.outputs.items():
        produced = outs_of.get(b.source)
        if produced is not None and b.name not in produced:
            out.append(issue(f"outputs.{k}: step '{b.source}' has no output '{b.name}'", b.source, "warning",
                             f"outputs.{k}"))
    return out


def validate(raw: str | dict[str, Any], registry: ExecutorRegistry | None,
             workflows_dir: Path | None = None) -> tuple[Recipe | None, list[dict[str, Any]]]:
    text = to_text(raw)
    if len(text.encode("utf-8")) > MAX_RECIPE_BYTES:
        return None, [issue(f"recipe larger than {MAX_RECIPE_BYTES // 1024} KB")]
    try:
        recipe = parse_recipe(text.encode("utf-8"))
    except RecipeError as e:
        return None, [issue(str(e).removeprefix("<recipe>: "), e.step)]
    except (TypeError, AttributeError, ValueError) as e:
        return None, [issue(f"malformed recipe: {e}")]
    return recipe, lint(recipe, registry, workflows_dir)


class RecipeLibrary:
    def __init__(self, builtin_dir: Path, user_dir: Path):
        self.builtin_dir = builtin_dir
        self.user_dir = user_dir
        self._sig: tuple[Any, ...] | None = None
        self.recipes: dict[str, Recipe] = {}
        self.sources: dict[str, Source] = {}
        self._builtin_ids: set[str] = set()
        self.reload()

    def _signature(self) -> tuple[Any, ...]:
        return _signature(self.builtin_dir), _signature(self.user_dir)

    def reload(self) -> None:
        self._sig = self._signature()
        builtin = load_recipes(self.builtin_dir)
        user = load_recipes(self.user_dir)
        self._builtin_ids = set(builtin)
        self.recipes = {**builtin, **user}
        self.sources = {rid: ("override" if rid in builtin else "user") if rid in user else "builtin"
                        for rid in self.recipes}

    def maybe_reload(self) -> None:
        if self._signature() != self._sig:
            self.reload()

    def get(self, rid: str) -> Recipe | None:
        self.maybe_reload()
        return self.recipes.get(rid)

    def source(self, rid: str) -> Source | None:
        self.maybe_reload()
        return self.sources.get(rid)

    def has_builtin(self, rid: str) -> bool:
        return rid in self._builtin_ids

    def text(self, rid: str) -> str | None:
        r = self.get(rid)
        if r is None or r.path is None:
            return None
        return r.path.read_text(encoding="utf-8")

    def _user_path(self, rid: str) -> Path:
        if not _ID.match(rid):
            raise RecipeError(f"invalid recipe id {rid!r}")
        return self.user_dir / f"{rid}.yaml"

    def save(self, rid: str, text: str) -> Recipe:
        """Writes the user copy atomically; built-in files are never touched."""
        recipe = parse_recipe(text.encode("utf-8"))
        if recipe.id != rid:
            raise RecipeError(f"recipe id '{recipe.id}' does not match '{rid}'")
        dest = self._user_path(rid)
        self.user_dir.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, dest)
        self.reload()
        return self.recipes[rid]

    def delete(self, rid: str) -> bool:
        """Removes the user copy; returns True when a built-in recipe with that id takes over again."""
        dest = self._user_path(rid)
        if not dest.is_file():
            raise LookupError(f"no user recipe '{rid}'")
        dest.unlink()
        self.reload()
        return rid in self.recipes
