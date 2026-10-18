from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from blake3 import blake3

from ..contracts import ExecutorRegistry, RecipeInfo

log = logging.getLogger(__name__)

BLOB_TYPES = {"image", "mesh"}
# `preset` is text naming an export preset; the app offers the saved presets as choices.
INPUT_TYPES = {"text", "int", "float", "bool", "enum", "preset"} | BLOB_TYPES
_PLACEHOLDER = re.compile(r"\$\{inputs\.([A-Za-z_][\w-]*)\s*(?:\|\s*([A-Za-z_]\w*)\s*)?\}")
_WHOLE = re.compile(r"^\$\{inputs\.([A-Za-z_][\w-]*)\}$")
LIST_FILTERS = {"lines"}
_ID = re.compile(r"^[A-Za-z_][\w-]*$")
# `${steps.<step>.<output>[.<field>...]}`: text of the step's first output blob, or a field of it when it is JSON.
STEP_REF = re.compile(r"\$\{steps\.([A-Za-z_][\w-]*)\.([A-Za-z_][\w-]*)((?:\.[\w-]+)*)\}")


class RecipeError(ValueError):
    def __init__(self, message: str, step: str | None = None):
        super().__init__(message)
        self.step = step


@dataclass(frozen=True)
class Binding:
    source: str  # step key, or "inputs"
    name: str  # output name, or input name when source == "inputs"

    @classmethod
    def parse(cls, raw: Any, where: str) -> Binding:
        if not isinstance(raw, str) or raw.count(".") != 1:
            raise RecipeError(f"{where}: binding must be '<step>.<output>' or 'inputs.<name>', got {raw!r}")
        src, name = raw.split(".")
        if not src or not name:
            raise RecipeError(f"{where}: bad binding {raw!r}")
        return cls(src, name)

    @property
    def parts(self) -> tuple[Binding, ...]:
        return (self,)

    def __str__(self) -> str:
        return f"{self.source}.{self.name}"


@dataclass(frozen=True)
class ConcatBinding:
    """`in: {images: [inputs.image, angles.images]}` — blobs concatenated in list order."""
    parts: tuple[Binding, ...]

    @property
    def source(self) -> str:
        return self.parts[0].source

    def __str__(self) -> str:
        return "+".join(str(p) for p in self.parts)


def parse_binding(raw: Any, where: str) -> Binding | ConcatBinding:
    if isinstance(raw, list):
        if len(raw) < 2:
            raise RecipeError(f"{where}: a binding list needs at least 2 entries")
        return ConcatBinding(tuple(Binding.parse(r, where) for r in raw))
    return Binding.parse(raw, where)


@dataclass
class StepSpec:
    id: str
    kind: str
    params: dict[str, Any] = field(default_factory=dict)
    bindings: dict[str, Binding | ConcatBinding] = field(default_factory=dict)
    map: str | None = None
    map_param: str | None = None
    refs: tuple[str, ...] = ()

    @property
    def deps(self) -> list[str]:
        out: list[str] = []
        for b in (p for bb in self.bindings.values() for p in bb.parts):
            if b.source != "inputs" and b.source not in out:
                out.append(b.source)
        out += [r for r in self.refs if r not in out]
        return out


@dataclass
class Recipe:
    id: str
    name: str
    description: str
    inputs: dict[str, dict[str, Any]]
    steps: list[StepSpec]  # topological order
    outputs: dict[str, Binding]
    hash: str
    path: Path | None = None

    def step(self, key: str) -> StepSpec:
        for s in self.steps:
            if s.id == key:
                return s
        raise KeyError(key)

    def coerce_inputs(self, given: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, spec in self.inputs.items():
            if name in given and given[name] is not None and given[name] != "":
                out[name] = coerce_value(name, spec, given[name])
            elif "default" in spec:
                out[name] = spec["default"]
            elif spec.get("required", True):
                raise RecipeError(f"input '{name}' is required")
            else:
                out[name] = None
        return out

    def resolve_params(self, step: StepSpec, inputs: dict[str, Any]) -> dict[str, Any]:
        return substitute(step.params, inputs)

    def info(self, registry: ExecutorRegistry | None = None) -> RecipeInfo:
        steps = []
        for s in self.steps:
            lane = None
            if registry is not None:
                try:
                    lane = registry.resolve(s.kind).lane.value
                except KeyError:
                    pass
            steps.append({"id": s.id, "kind": s.kind, "lane": lane, "deps": s.deps, "map": s.map,
                          "map_param": s.map_param})
        return RecipeInfo(id=self.id, name=self.name, description=self.description, inputs=self.inputs,
                          steps=steps, hash=self.hash)


def coerce_value(name: str, spec: dict[str, Any], v: Any) -> Any:
    t = spec.get("type", "text")
    try:
        match t:
            case "text" | "preset":
                if not isinstance(v, (str, int, float)):
                    raise TypeError
                v = str(v)
            case "int":
                if isinstance(v, bool) or (isinstance(v, float) and not v.is_integer()):
                    raise TypeError
                v = int(v)
            case "float":
                if isinstance(v, bool):
                    raise TypeError
                v = float(v)
            case "bool":
                if isinstance(v, str):
                    low = v.strip().lower()
                    if low not in ("true", "false", "1", "0", "yes", "no"):
                        raise TypeError
                    v = low in ("true", "1", "yes")
                elif isinstance(v, (bool, int)):
                    v = bool(v)
                else:
                    raise TypeError
            case "image" | "mesh":
                if isinstance(v, dict) and "hash" in v:
                    v = v["hash"]
                if isinstance(v, list):
                    v = [x["hash"] if isinstance(x, dict) else x for x in v]
                    if not v or not all(isinstance(x, str) and x for x in v):
                        raise TypeError
                elif not isinstance(v, str) or not v:
                    raise TypeError
            case "enum":
                pass
    except (TypeError, ValueError):
        raise RecipeError(f"input '{name}': expected {t}, got {v!r}") from None
    if "min" in spec and isinstance(v, (int, float)) and v < spec["min"]:
        raise RecipeError(f"input '{name}': {v} < min {spec['min']}")
    if "max" in spec and isinstance(v, (int, float)) and v > spec["max"]:
        raise RecipeError(f"input '{name}': {v} > max {spec['max']}")
    if "options" in spec and v not in spec["options"]:
        raise RecipeError(f"input '{name}': {v!r} not in {spec['options']}")
    return v


def split_lines(v: Any) -> list[str]:
    if v is None:
        return []
    return [ln.strip() for ln in str(v).splitlines() if ln.strip()]


def _scalar_sub(value: str, inputs: dict[str, Any]) -> str:
    return _PLACEHOLDER.sub(lambda mm: "" if inputs.get(mm.group(1)) is None else str(inputs[mm.group(1)]), value)


def substitute(value: Any, inputs: dict[str, Any]) -> Any:
    """`${inputs.x | lines}` turns the whole string into a list: one copy per non-empty line of x."""
    if isinstance(value, str):
        m = _WHOLE.match(value)
        if m:
            return inputs.get(m.group(1))
        lm = next((mm for mm in _PLACEHOLDER.finditer(value) if mm.group(2)), None)
        if lm is None:
            return _scalar_sub(value, inputs)
        head, tail = _scalar_sub(value[:lm.start()], inputs), _scalar_sub(value[lm.end():], inputs)
        return [head + ln + tail for ln in split_lines(inputs.get(lm.group(1)))]
    if isinstance(value, dict):
        return {k: substitute(v, inputs) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, inputs) for v in value]
    return value


def find_step_refs(value: Any) -> list[tuple[str, str, str, tuple[str, ...]]]:
    """(placeholder text, step, output, field path) for every `${steps.…}` in a params tree."""
    if isinstance(value, str):
        return [(m.group(0), m.group(1), m.group(2), tuple(f for f in m.group(3).split(".") if f))
                for m in STEP_REF.finditer(value)]
    if isinstance(value, dict):
        return [r for v in value.values() for r in find_step_refs(v)]
    if isinstance(value, list):
        return [r for v in value for r in find_step_refs(v)]
    return []


def ref_value(raw: bytes, path: tuple[str, ...]) -> Any:
    text = raw.decode("utf-8")
    if not path:
        return text
    cur: Any = json.loads(text)
    for f in path:
        if isinstance(cur, list):
            cur = cur[int(f)]
        elif isinstance(cur, dict):
            cur = cur[f]
        else:
            raise KeyError(f)
    return cur


def substitute_refs(value: Any, values: dict[str, Any]) -> Any:
    """A placeholder that is the whole string keeps the value's JSON type; embedded ones become text."""
    if isinstance(value, str):
        if value in values:
            return values[value]

        def text(m: re.Match[str]) -> str:
            v = values.get(m.group(0), m.group(0))
            return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)

        return STEP_REF.sub(text, value)
    if isinstance(value, dict):
        return {k: substitute_refs(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute_refs(v, values) for v in value]
    return value


def _placeholders(value: Any) -> set[str]:
    if isinstance(value, str):
        return {m.group(1) for m in _PLACEHOLDER.finditer(value)}
    if isinstance(value, dict):
        return set().union(*(_placeholders(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_placeholders(v) for v in value)) if value else set()
    return set()


def _list_placeholders(value: Any) -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(m.group(1), m.group(2)) for m in _PLACEHOLDER.finditer(value) if m.group(2)]
    if isinstance(value, dict):
        return [p for v in value.values() for p in _list_placeholders(v)]
    if isinstance(value, list):
        return [p for v in value for p in _list_placeholders(v)]
    return []


def _check_list_params(where: str, sid: str, params: dict[str, Any], map_param: Any,
                       inputs: dict[str, dict[str, Any]]) -> None:
    if map_param is not None:
        if not isinstance(map_param, str) or map_param not in params:
            raise RecipeError(f"{where}: step {sid} map_param {map_param!r} is not one of its params")
        v = params[map_param]
        listy = isinstance(v, list) or (isinstance(v, str) and len(_list_placeholders(v)) == 1)
        if not listy:
            raise RecipeError(f"{where}: step {sid} map_param '{map_param}' must be a list or one "
                              "'${inputs.x | lines}' placeholder")
    for key, v in params.items():
        found = _list_placeholders(v)
        if not found:
            continue
        if key != map_param or not isinstance(v, str) or len(found) > 1:
            raise RecipeError(f"{where}: step {sid} param '{key}': list filters only allowed once, "
                              "in the map_param value")
        name, flt = found[0]
        if flt not in LIST_FILTERS:
            raise RecipeError(f"{where}: step {sid} unknown filter '{flt}'")
        if inputs.get(name, {}).get("type") != "text":
            raise RecipeError(f"{where}: step {sid} filter '{flt}' needs a text input, got '{name}'")


def parse_recipe(raw_bytes: bytes, path: Path | None = None) -> Recipe:
    where = path.name if path else "<recipe>"
    try:
        doc = yaml.safe_load(raw_bytes)
    except yaml.YAMLError as e:
        raise RecipeError(f"{where}: invalid YAML: {e}") from None
    if not isinstance(doc, dict):
        raise RecipeError(f"{where}: top level must be a mapping")
    rid = doc.get("id")
    if not isinstance(rid, str) or not _ID.match(rid):
        raise RecipeError(f"{where}: missing/invalid id")

    inputs: dict[str, dict[str, Any]] = {}
    for name, spec in (doc.get("inputs") or {}).items():
        if not _ID.match(str(name)):
            raise RecipeError(f"{where}: bad input name {name!r}")
        spec = dict(spec or {})
        spec.setdefault("type", "text")
        if spec["type"] not in INPUT_TYPES:
            raise RecipeError(f"{where}: input '{name}' has unknown type {spec['type']!r}")
        if spec["type"] == "enum" and not spec.get("options"):
            raise RecipeError(f"{where}: enum input '{name}' needs options")
        if "default" in spec and spec["default"] is not None:
            spec["default"] = coerce_value(name, spec, spec["default"])
        inputs[str(name)] = spec

    raw_steps = doc.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise RecipeError(f"{where}: steps must be a non-empty list")
    specs: list[StepSpec] = []
    seen: set[str] = set()
    for i, rs in enumerate(raw_steps):
        if not isinstance(rs, dict):
            raise RecipeError(f"{where}: step #{i} must be a mapping")
        sid, kind = rs.get("id"), rs.get("kind")
        if not isinstance(sid, str) or not _ID.match(sid) or sid == "inputs":
            raise RecipeError(f"{where}: step #{i} has invalid id {sid!r}")
        if sid in seen:
            raise RecipeError(f"{where}: duplicate step id {sid!r}", step=sid)
        seen.add(sid)
        with _step_errors(sid):
            specs.append(_parse_step(where, sid, kind, rs, inputs))

    for s in specs:
        with _step_errors(s.id):
            _check_step_links(where, s, inputs, seen)

    outputs = {str(k): Binding.parse(v, f"{where}: outputs.{k}") for k, v in (doc.get("outputs") or {}).items()}
    for k, b in outputs.items():
        if b.source not in seen:
            raise RecipeError(f"{where}: outputs.{k} references unknown step {b.source!r}")

    return Recipe(id=rid, name=str(doc.get("name") or rid), description=str(doc.get("description") or ""),
                  inputs=inputs, steps=_toposort(specs, where), outputs=outputs,
                  hash=blake3(raw_bytes).hexdigest(), path=path)



@contextmanager
def _step_errors(sid: str) -> Iterator[None]:
    try:
        yield
    except RecipeError as e:
        if e.step is None:
            e.step = sid
        raise


def _parse_step(where: str, sid: str, kind: Any, rs: dict[str, Any], inputs: dict[str, dict[str, Any]]) -> StepSpec:
    if not isinstance(kind, str) or not kind:
        raise RecipeError(f"{where}: step {sid} missing kind")
    params = rs.get("params") or {}
    if not isinstance(params, dict):
        raise RecipeError(f"{where}: step {sid} params must be a mapping")
    unknown = _placeholders(params) - inputs.keys()
    if unknown:
        raise RecipeError(f"{where}: step {sid} references unknown inputs {sorted(unknown)}")
    bindings = {str(k): parse_binding(v, f"{where}: step {sid} in.{k}") for k, v in (rs.get("in") or {}).items()}
    mp = rs.get("map")
    if mp is not None and mp not in bindings:
        raise RecipeError(f"{where}: step {sid} map '{mp}' is not one of its inputs")
    mpp = rs.get("map_param")
    _check_list_params(where, sid, params, mpp, inputs)
    if mp is not None and mpp is not None:
        raise RecipeError(f"{where}: step {sid} cannot use both map and map_param")
    refs = tuple(dict.fromkeys(r[1] for r in find_step_refs(params)))
    return StepSpec(sid, kind, params, bindings, mp, mpp, refs)


def _check_step_links(where: str, s: StepSpec, inputs: dict[str, dict[str, Any]], seen: set[str]) -> None:
    for name, b in ((n, p) for n, bb in s.bindings.items() for p in bb.parts):
        if b.source == "inputs":
            if b.name not in inputs or inputs[b.name]["type"] not in BLOB_TYPES:
                raise RecipeError(f"{where}: step {s.id} in.{name} must bind an image/mesh input, got {b}")
        elif b.source not in seen:
            raise RecipeError(f"{where}: step {s.id} in.{name} references unknown step {b.source!r}")
    for r in s.refs:
        if r not in seen or r == s.id:
            raise RecipeError(f"{where}: step {s.id} params reference unknown step {r!r}")

def _toposort(specs: list[StepSpec], where: str) -> list[StepSpec]:
    by_id = {s.id: s for s in specs}
    indeg = {s.id: len(s.deps) for s in specs}
    order: list[StepSpec] = []
    queue = [s.id for s in specs if indeg[s.id] == 0]
    while queue:
        sid = queue.pop(0)
        order.append(by_id[sid])
        for s in specs:
            if sid in s.deps:
                indeg[s.id] -= 1
                if indeg[s.id] == 0:
                    queue.append(s.id)
    if len(order) != len(specs):
        stuck = sorted(k for k, v in indeg.items() if v > 0)
        raise RecipeError(f"{where}: dependency cycle among steps {stuck}")
    return order


def load_recipe_file(path: Path) -> Recipe:
    return parse_recipe(path.read_bytes(), path)


def load_recipes(recipes_dir: Path) -> dict[str, Recipe]:
    out: dict[str, Recipe] = {}
    if not recipes_dir.is_dir():
        return out
    for p in sorted(recipes_dir.glob("*.y*ml")):
        try:
            r = load_recipe_file(p)
        except RecipeError as e:
            log.error("skipping recipe: %s", e)
            continue
        if r.id in out:
            log.error("skipping recipe %s: duplicate id %s", p.name, r.id)
            continue
        out[r.id] = r
    return out
