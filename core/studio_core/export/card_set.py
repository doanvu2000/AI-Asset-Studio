"""Card set export (op.card_set): per card a Unity UGUI prefab (Art Image under a Frame Image with a transparent window,
legacy Text for name / type / rules / cost / attack / health, swapped for TextMeshPro by an Editor script when the
project has TMP) and a Godot Control scene (TextureRects + Labels with a
SystemFont matching the baked PNGs), a data asset (ScriptableObject / Resource), the baked fronts, the shared back
and an atlas of fronts + back."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from studio_core.export.source import Files, Source
from studio_core.export.sheet import cs_namespace, stable_hex

SPRITE_ID = 21300000
ASSET_ID = 11400000
LAYER_UI = 5
# Fixed so several card sets in one Unity project share one CardSetData script.
SCRIPT_GUID = stable_hex("unity", "AssetStudio", "Scripts/CardSetData.cs")
ROLES = ("name", "type", "desc", "cost", "attack", "health")
# com.unity.ugui script GUIDs (same in every Unity that ships uGUI as a package) and the built-in LegacyRuntime font.
UGUI_IMAGE, UGUI_TEXT, UGUI_OUTLINE = ("fe87c0e1cc204ed48ad3b37840f39efc", "5f7201a12d95ffc409449d95f23cf332",
                                      "e19747de3f5aca642ab2be37e372fb86")
UGUI_SCALER = "0cd44c1031e13a943bb63640046fad76"
LEGACY_FONT = "{fileID: 10102, guid: 0000000000000000e000000000000000, type: 0}"
NODE = {"name": "Name", "type": "TypeLine", "desc": "Rules", "cost": "Cost", "attack": "Attack", "health": "Health"}
FALLBACK_FONTS = ("Segoe UI", "Arial", "Noto Sans", "DejaVu Sans")


def card_set(step: Any, blobs: Any) -> tuple[dict[str, Any] | None, dict[str, Path]]:
    """card_set.json and its images by key (front:<id>, art:<id>, frame:<key>, back), also behind a gate."""
    from studio_core.ops.card_set import SET_FILE, SET_TYPE

    for refs in step.outputs.values():
        for ref in refs:
            if (ref.name or "") != SET_FILE:
                continue
            try:
                doc = json.loads(blobs.path(ref.hash).read_bytes())
                if doc.get("type") != SET_TYPE or not doc.get("cards"):
                    return None, {}
                files = {"back": blobs.path(str(doc["back"]["hash"]))}
                for k, f in doc["frames"].items():
                    files[f"frame:{k}"] = blobs.path(str(f["hash"]))
                for c in doc["cards"]:
                    files[f"front:{c['id']}"] = blobs.path(str(c["front"]["hash"]))
                    files[f"art:{c['id']}"] = blobs.path(str(c["art_image"]["hash"]))
                if not all(p.is_file() for p in files.values()):
                    return None, {}
                return doc, files
            except (OSError, ValueError, KeyError, TypeError):
                return None, {}
    return None, {}


def rel(src: Source, key: str) -> str:
    kind, _, name = key.partition(":")
    return {"back": "Cards/card_back.png", "front": f"Cards/Fronts/{name}.png", "art": f"Cards/Art/art_{name}.png",
            "frame": f"Cards/Frames/{name}.png"}[kind]


def index_json(src: Source) -> bytes:
    doc = src.cards or {}
    out = {"asset": src.asset, "size": doc.get("size"), "mm": doc.get("mm"), "fonts": doc.get("fonts"),
           "rarities": doc.get("rarities"), "back": rel(src, "back"), "cards": []}
    for c in doc.get("cards") or []:
        out["cards"].append({k: c.get(k) for k in ("id", "name", "description", "cost", "attack", "health", "rarity",
                                                    "rarity_index", "type", "type_label", "flavor", "art_rect")}
                            | {"front": rel(src, f"front:{c['id']}"), "art": rel(src, f"art:{c['id']}"),
                               "frame": rel(src, f"frame:{c['frame']}"),
                               "texts": {k: {kk: v[kk] for kk in ("text", "rect", "size", "color", "wrap")}
                                         for k, v in (c.get("texts") or {}).items()}})
    return json.dumps(out, ensure_ascii=False, indent=1).encode("utf-8")


def _f(v: float) -> str:
    return f"{float(v):.6g}"


def _rgb(h: str) -> tuple[float, float, float]:
    h = (h or "#ffffff").lstrip("#")
    return int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255


def _anchors(rect: list[float]) -> tuple[float, float, float, float]:
    x, y, w, h = rect
    return x, 1 - (y + h), x + w, 1 - y


class _Prefab:
    """GameObjects with RectTransforms in a tree; written once every child list is known."""

    def __init__(self, scope: str):
        from studio_core.export.unity import file_id

        self.fid = lambda *p: file_id("card_set", scope, *p)
        self.nodes: list[tuple[Any, ...]] = []
        self.kids: dict[str | None, list[str]] = {}

    def _head(self, cls: int, fid: int, kind: str) -> str:
        return (f"--- !u!{cls} &{fid}\n{kind}:\n  m_ObjectHideFlags: 0\n  m_CorrespondingSourceObject: {{fileID: 0}}\n"
                "  m_PrefabInstance: {fileID: 0}\n  m_PrefabAsset: {fileID: 0}\n")

    def node(self, key: str, name: str, parent: str | None, anchors: tuple[float, float, float, float],
             comps: list[tuple[str, str]], size: tuple[float, float] = (0, 0), pos: tuple[float, float] = (0, 0),
             scale: float = 1.0, raw: list[tuple[int, str, str]] | None = None) -> None:
        """comps: (kind, MonoBehaviour body with m_Script); raw: (class id, kind, body), e.g. a Canvas."""
        self.kids.setdefault(parent, []).append(key)
        self.nodes.append((key, name, parent, anchors, size, pos, scale,
                           [(114, k, b) for k, b in comps] + list(raw or [])))

    def _docs(self, key: str, name: str, parent: str | None, a: tuple[float, float, float, float],
              size: tuple[float, float], pos: tuple[float, float], scale: float,
              comps: list[tuple[int, str, str]]) -> str:
        go, rt, cr = self.fid(key, "go"), self.fid(key, "rt"), self.fid(key, "cr")
        graphic = any(c == 114 for c, _, _ in comps)
        ids = [self.fid(key, k) for _, k, _ in comps]
        all_c = [rt] + ([cr] if graphic else []) + ids
        kids = "".join(f"\n  - {{fileID: {self.fid(k, 'rt')}}}" for k in self.kids.get(key, []))
        out = [self._head(1, go, "GameObject") + "  serializedVersion: 6\n  m_Component:\n"
               + "".join(f"  - component: {{fileID: {c}}}\n" for c in all_c)
               + f"  m_Layer: {LAYER_UI}\n  m_Name: {name}\n  m_TagString: Untagged\n  m_Icon: {{fileID: 0}}\n"
               "  m_NavMeshLayer: 0\n  m_StaticEditorFlags: 0\n  m_IsActive: 1\n",
               self._head(224, rt, "RectTransform") + f"  m_GameObject: {{fileID: {go}}}\n"
               "  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}\n  m_LocalPosition: {x: 0, y: 0, z: 0}\n"
               f"  m_LocalScale: {{x: {_f(scale)}, y: {_f(scale)}, z: 1}}\n  m_ConstrainProportionsScale: 0\n"
               f"  m_Children:{kids if kids else ' []'}\n"
               f"  m_Father: {{fileID: {self.fid(parent, 'rt') if parent else 0}}}\n"
               "  m_LocalEulerAnglesHint: {x: 0, y: 0, z: 0}\n"
               f"  m_AnchorMin: {{x: {_f(a[0])}, y: {_f(a[1])}}}\n  m_AnchorMax: {{x: {_f(a[2])}, y: {_f(a[3])}}}\n"
               f"  m_AnchoredPosition: {{x: {_f(pos[0])}, y: {_f(pos[1])}}}\n"
               f"  m_SizeDelta: {{x: {_f(size[0])}, y: {_f(size[1])}}}\n  m_Pivot: {{x: 0.5, y: 0.5}}\n"]
        if graphic:
            out.append(self._head(222, cr, "CanvasRenderer") + f"  m_GameObject: {{fileID: {go}}}\n"
                       "  m_CullTransparentMesh: 1\n")
        for (cls, _, body), cid in zip(comps, ids):
            kind = {114: "MonoBehaviour", 223: "Canvas"}[cls]
            extra = "  m_Enabled: 1\n" + ("  m_EditorHideFlags: 0\n" if cls == 114 else "")
            out.append(self._head(cls, cid, kind) + f"  m_GameObject: {{fileID: {go}}}\n" + extra + body)
        return "".join(out)

    def text(self) -> str:
        from studio_core.export.unity import HEADER

        return HEADER + "".join(self._docs(*n) for n in self.nodes)


CANVAS = """  serializedVersion: 3
  m_RenderMode: 0
  m_Camera: {fileID: 0}
  m_PlaneDistance: 100
  m_PixelPerfect: 0
  m_ReceivesEvents: 1
  m_OverrideSorting: 0
  m_OverridePixelPerfect: 0
  m_SortingBucketNormalizedSize: 0
  m_VertexColorAlwaysGammaSpace: 0
  m_AdditionalShaderChannelsFlag: 25
  m_UpdateRectTransformForStandalone: 0
  m_SortingLayerID: 0
  m_SortingOrder: 0
  m_TargetDisplay: 0
"""
SCALER = """  m_UiScaleMode: 1
  m_ReferencePixelsPerUnit: 100
  m_ScaleFactor: 1
  m_ReferenceResolution: {x: 1920, y: 1080}
  m_ScreenMatchMode: 0
  m_MatchWidthOrHeight: 0.5
  m_PhysicalUnit: 3
  m_FallbackScreenDPI: 96
  m_DefaultSpriteDPI: 96
  m_DynamicPixelsPerUnit: 1
  m_PresetInfoIsWorld: 0
"""


def _sprite(g: str) -> str:
    return f"{{fileID: {SPRITE_ID}, guid: {g}, type: 3}}"


def _script(g: str) -> str:
    return f"  m_Script: {{fileID: 11500000, guid: {g}, type: 3}}\n  m_Name: \n  m_EditorClassIdentifier: \n"


def _graphic(rgb: tuple[float, float, float], ray: bool) -> str:
    return (f"  m_Material: {{fileID: 0}}\n  m_Color: {{r: {_f(rgb[0])}, g: {_f(rgb[1])}, b: {_f(rgb[2])}, a: 1}}\n"
            f"  m_RaycastTarget: {int(ray)}\n  m_RaycastPadding: {{x: 0, y: 0, z: 0, w: 0}}\n  m_Maskable: 1\n"
            "  m_OnCullStateChanged:\n    m_PersistentCalls:\n      m_Calls: []\n")


def _image(g: str, ray: bool = False) -> str:
    return _script(UGUI_IMAGE) + _graphic((1, 1, 1), ray) + f"""  m_Sprite: {_sprite(g)}
  m_Type: 0
  m_PreserveAspect: 0
  m_FillCenter: 1
  m_FillMethod: 4
  m_FillAmount: 1
  m_FillClockwise: 1
  m_FillOrigin: 0
  m_UseSpriteMesh: 0
  m_PixelsPerUnitMultiplier: 1
"""


def _text(t: dict[str, Any], bold: bool) -> str:
    size = int(t.get("size") or 24)
    return _script(UGUI_TEXT) + _graphic(_rgb(str(t.get("color") or "#ffffff")), False) + f"""  m_FontData:
    m_Font: {LEGACY_FONT}
    m_FontSize: {size}
    m_FontStyle: {1 if bold else 0}
    m_BestFit: 1
    m_MinSize: {max(1, round(size * 0.55))}
    m_MaxSize: {size}
    m_Alignment: 4
    m_AlignByGeometry: 0
    m_RichText: 0
    m_HorizontalOverflow: 0
    m_VerticalOverflow: 0
    m_LineSpacing: 1
  m_Text: {json.dumps(str(t.get("text") or ""), ensure_ascii=False)}
"""


def _outline(color: str, size: int) -> str:
    r, g, b = _rgb(color)
    d = max(1, round(size * 0.05))
    return _script(UGUI_OUTLINE) + f"""  m_EffectColor: {{r: {_f(r)}, g: {_f(g)}, b: {_f(b)}, a: 1}}
  m_EffectDistance: {{x: {d}, y: -{d}}}
  m_UseGraphicAlpha: 1
"""


def _card_nodes(p: _Prefab, key: str, parent: str | None, card: dict[str, Any], guids: dict[str, str],
                size: tuple[float, float], pos: tuple[float, float] = (0, 0), scale: float = 1.0) -> None:
    p.node(key, f"Card_{card['id']}", parent, (0.5, 0.5, 0.5, 0.5), [], size, pos, scale)
    p.node(f"{key}/art", "Art", key, _anchors(card["art_rect"]), [("img", _image(guids[f"art:{card['id']}"]))])
    p.node(f"{key}/frame", "Frame", key, (0, 0, 1, 1), [("img", _image(guids[f"frame:{card['frame']}"], True))])
    for role in ROLES:
        t = (card.get("texts") or {}).get(role)
        if not t:
            continue
        comps = [("txt", _text(t, role in ("name", "cost", "attack", "health")))]
        if t.get("stroke"):
            comps.append(("outline", _outline(str(t.get("stroke_color") or "#000000"), int(t.get("size") or 24))))
        p.node(f"{key}/{role}", NODE[role], key, _anchors(t["rect"]), comps)


def _size(src: Source) -> tuple[int, int]:
    w, h = (src.cards or {}).get("size") or (756, 1056)
    return int(w), int(h)


def card_prefab(src: Source, card: dict[str, Any], guids: dict[str, str]) -> str:
    p = _Prefab(f"{src.asset}:{card['id']}")
    _card_nodes(p, "root", None, card, guids, _size(src))
    return p.text()


def back_prefab(src: Source, g: str) -> str:
    p = _Prefab(f"{src.asset}:back")
    p.node("root", "CardBack", None, (0.5, 0.5, 0.5, 0.5), [("img", _image(g, True))], _size(src))
    return p.text()


def demo_prefab(src: Source, guids: dict[str, str]) -> str:
    """A Canvas (1920x1080 reference) dealing the first cards and the back in a row, to see the set at a glance; the
    cards in it are copies, the per-card prefabs are the ones to edit."""
    cards = ((src.cards or {}).get("cards") or [])[:5]
    w, h = _size(src)
    n = len(cards) + 1
    # The row must fit the 4:3 tablet width too (1440 at the 1080-high reference).
    scale = min(1.0, 1350 / (n * w * 1.08), 900 / h)
    step = w * scale * 1.08
    x0 = -(n - 1) * step / 2
    p = _Prefab(f"{src.asset}:demo")
    p.node("root", f"{src.asset}_CardsDemo", None, (0, 0, 0, 0), [("scaler", _script(UGUI_SCALER) + SCALER)],
           raw=[(223, "canvas", CANVAS)])
    for i, c in enumerate(cards):
        _card_nodes(p, f"c{i}", "root", c, guids, (w, h), (x0 + i * step, 0), scale)
    p.node("back", "CardBack", "root", (0.5, 0.5, 0.5, 0.5), [("img", _image(guids["back"]))], (w, h),
           (x0 + len(cards) * step, 0), scale)
    return p.text()


UNITY_SCRIPT = cs_namespace("""using System;
using UnityEngine;

// Generated by AI Asset Studio: one card set (rules text, stats, rarity, type) with its sprites and card prefabs.
[CreateAssetMenu(menuName = "Asset Studio/Card Set", fileName = "CardSet")]
public class CardSetData : ScriptableObject
{
    [Serializable]
    public class Card
    {
        public string id;
        public string cardName;
        [TextArea] public string description;
        public int cost = -1;
        public int attack = -1;
        public int health = -1;
        public string rarity;
        public int rarityIndex;
        public string type;
        public string typeLabel;
        [TextArea] public string flavor;
        public Sprite front;
        public Sprite art;
        public GameObject prefab;
    }

    public Vector2 cardSize;
    public Sprite back;
    public Card[] cards;

    public Card Find(string id)
    {
        foreach (var c in cards)
            if (c.id == id) return c;
        return null;
    }
}
""")


def _q(s: Any) -> str:
    return json.dumps(str(s or ""))


def unity_asset(src: Source, guids: dict[str, str], prefab_guids: dict[str, str]) -> str:
    from studio_core.export.unity import HEADER

    doc = src.cards or {}
    w, h = doc.get("size") or (756, 1056)
    out = [HEADER, f"--- !u!114 &{ASSET_ID}\nMonoBehaviour:\n  m_ObjectHideFlags: 0\n",
           "  m_CorrespondingSourceObject: {fileID: 0}\n  m_PrefabInstance: {fileID: 0}\n  m_PrefabAsset: {fileID: 0}\n",
           "  m_GameObject: {fileID: 0}\n  m_Enabled: 1\n  m_EditorHideFlags: 0\n",
           f"  m_Script: {{fileID: 11500000, guid: {SCRIPT_GUID}, type: 3}}\n  m_Name: {src.asset}_cards\n",
           "  m_EditorClassIdentifier: \n", f"  cardSize: {{x: {w}, y: {h}}}\n", f"  back: {_sprite(guids['back'])}\n",
           "  cards:\n"]
    from studio_core.export.unity import file_id

    for c in doc.get("cards") or []:
        num = {k: (-1 if c.get(k) is None else int(c[k])) for k in ("cost", "attack", "health")}
        root_go = file_id("card_set", f"{src.asset}:{c['id']}", "root", "go")
        out.append(f"  - id: {c['id']}\n    cardName: {_q(c['name'])}\n    description: {_q(c['description'])}\n"
                   f"    cost: {num['cost']}\n    attack: {num['attack']}\n    health: {num['health']}\n"
                   f"    rarity: {c['rarity']}\n    rarityIndex: {int(c['rarity_index'])}\n    type: {c['type']}\n"
                   f"    typeLabel: {_q(c.get('type_label'))}\n    flavor: {_q(c.get('flavor'))}\n"
                   f"    front: {_sprite(guids['front:' + c['id']])}\n    art: {_sprite(guids['art:' + c['id']])}\n"
                   f"    prefab: {{fileID: {root_go}, guid: {prefab_guids[c['id']]}, type: 3}}\n")
    return "".join(out)


def _atlas(src: Source, engine: str) -> tuple[Files, list[str]]:
    from PIL import Image

    from studio_core.export.atlas import AtlasError, AtlasOptions, Sprite, engine_files, pack, stats_lines

    sprites = []
    for c in (src.cards or {}).get("cards") or []:
        with Image.open(src.cards_files[f"front:{c['id']}"]) as im:
            img = im.convert("RGBA")
        sprites.append(Sprite(c["id"], img, (img.width / 2, img.height / 2)))
    with Image.open(src.cards_files["back"]) as im:
        img = im.convert("RGBA")
    sprites.append(Sprite("card_back", img, (img.width / 2, img.height / 2)))
    try:
        atlas = pack(sprites, f"{src.asset}_cards_atlas", AtlasOptions(max_size=4096, trim=False))
    except AtlasError:
        return [], []
    files = [(f"Cards/Atlas/{p}", data) for p, data in engine_files(atlas, engine, src.ppu, src.uid)
             if p != "README.txt"]
    return files, stats_lines(atlas)


# ui-screen's Text -> TextMeshPro converter, retargeted from one <asset>.prefab to every card prefab (one marker each)
# and to one outline material per stroke colour (cost / attack / health / type lines are outlined in different colours).
TMP_PATCHES = (
    ("once __ASSET__.prefab is imported, swaps its", "once the __ASSET__ card prefabs are imported, swaps their"),
    ("Reimport __ASSET___ui_tmp.json", "Reimport the __ASSET___*_ui_tmp.json markers"),
    ("    const string Asset = __ASSET_LIT__;\n",
     "    const string Asset = __ASSET_LIT__;\n\n    static bool Mine(string p)\n    {\n"
     "        var f = Path.GetFileName(p);\n"
     "        return f == Asset + \"_CardsDemo_ui_tmp.json\"\n"
     "               || (p.Contains(\"/Cards/Prefabs/\") && f.StartsWith(Asset + \"_\") && f.EndsWith(\"_ui_tmp.json\"));\n"
     "    }\n"),
    ('if (!p.EndsWith("/" + Asset + "_ui_tmp.json")) continue;', "if (!Mine(p)) continue;"),
    ('if (p.EndsWith("/" + Asset + "_ui_tmp.json")) Convert(p);', "if (Mine(p)) Convert(p);"),
    ('var prefab = dir + "/" + Asset + ".prefab";',
     'var prefab = marker.Substring(0, marker.Length - "_ui_tmp.json".Length) + ".prefab";'),
    ('Asset + "_TMP_Outline.mat"', 'Asset + "_TMP_Outline_" + ColorUtility.ToHtmlStringRGB(color) + ".mat"'),
    ("outline = outline ?? OutlineMaterial(font, dir, outlineColor);",
     "outline = OutlineMaterial(font, dir, outlineColor);"),
)


def tmp_cs(asset: str) -> str:
    from studio_core.export.sheet import ident
    from studio_core.export.ui_screen import TMP_CS

    cs = TMP_CS
    for old, new in TMP_PATCHES:
        if old not in cs:
            raise RuntimeError(f"ui_screen.TMP_CS changed; card_set patch no longer applies: {old!r}")
        cs = cs.replace(old, new)
    return cs_namespace(cs.replace("__CLASS__", f"CardTmp_{ident(asset, 'Cards')}")
                        .replace("__ASSET_LIT__", json.dumps(asset)).replace("__ASSET__", asset))


def _tmp_files(src: Source, prefabs: list[str], guid_of: Any) -> Files:
    from studio_core.export.npc import script_meta
    from studio_core.export.ui_screen import TMP_MARKER

    cs_rel = f"Editor/AssetStudioCardTmp_{src.asset}.cs"
    files: Files = [(f"{r[:-len('.prefab')]}{TMP_MARKER}", json.dumps({"prefab": Path(r).name}).encode())
                    for r in prefabs]
    return files + [(cs_rel, tmp_cs(src.asset).encode()), (f"{cs_rel}.meta", script_meta(guid_of(cs_rel)).encode())]


def unity_files(src: Source) -> tuple[Files, list[str]]:
    from studio_core.export.npc import script_meta
    from studio_core.export.ui_screen import TMP_MARKER
    from studio_core.export.unity import guid, native_meta, prefab_meta, texture_meta

    files: Files = []
    guids: dict[str, str] = {}
    for key, path in src.cards_files.items():
        r = rel(src, key)
        guids[key] = guid(src.uid, src.asset, r)
        from PIL import Image

        with Image.open(path) as im:
            size = im.size
        files += [(r, path), (f"{r}.meta", texture_meta(f"{src.asset}_{Path(r).stem}", guids[key], None, size,
                                                        src.ppu, False).encode())]
    prefab_guids: dict[str, str] = {}
    for c in (src.cards or {}).get("cards") or []:
        r = f"Cards/Prefabs/{src.asset}_{c['id']}.prefab"
        prefab_guids[c["id"]] = guid(src.uid, src.asset, r)
        files += [(r, card_prefab(src, c, guids).encode("utf-8")), (f"{r}.meta", prefab_meta(prefab_guids[c["id"]]).encode())]
    back_rel = f"Cards/Prefabs/{src.asset}_back.prefab"
    demo_rel = f"{src.asset}_CardsDemo.prefab"
    files += [(back_rel, back_prefab(src, guids["back"]).encode()),
              (f"{back_rel}.meta", prefab_meta(guid(src.uid, src.asset, back_rel)).encode()),
              (demo_rel, demo_prefab(src, guids).encode("utf-8")),
              (f"{demo_rel}.meta", prefab_meta(guid(src.uid, src.asset, demo_rel)).encode())]
    asset_rel = f"Cards/{src.asset}_cards.asset"
    files += [(f"Cards/{src.asset}_cards.json", index_json(src)),
              ("Scripts/CardSetData.cs", UNITY_SCRIPT.encode()),
              ("Scripts/CardSetData.cs.meta", script_meta(SCRIPT_GUID).encode()),
              (asset_rel, unity_asset(src, guids, prefab_guids).encode("utf-8")),
              (f"{asset_rel}.meta", native_meta(guid(src.uid, src.asset, asset_rel), ASSET_ID).encode())]
    files += _tmp_files(src, [r for r, _ in files if r.endswith(".prefab") and r != back_rel],
                        lambda r: guid(src.uid, src.asset, r))
    atlas_files, atlas_notes = _atlas(src, "unity")
    files += atlas_files
    doc = src.cards or {}
    w, h = doc.get("size") or (0, 0)
    n = len(doc.get("cards") or [])
    note = (f"- Cards/: {n} thẻ {w}×{h} px (tỉ lệ 63×88). Cards/Prefabs/{src.asset}_<id>.prefab: thẻ UGUI ghép lớp "
            "(Art dưới Frame có ô trong suốt, Text tên / dòng loại / luật / chi phí / công / máu, Best Fit bật), kéo vào "
            "một Canvas. Chữ: project có TextMeshPro + font TMP (TMP Essentials) thì "
            f"Editor/AssetStudioCardTmp_{src.asset}.cs tự đổi Text sang TextMeshPro trong mọi prefab thẻ + CardsDemo "
            "(giữ cỡ, màu, căn giữa, Auto Size, viền theo màu từng dòng qua material outline); chưa có TMP thì giữ UI Text (legacy) LegacyRuntime + Best Fit — import "
            f"TMP Essentials rồi Reimport các file *{TMP_MARKER}. Font TMP mặc định (LiberationSans SDF) cần bản Fallback "
            "động để có chữ Việt có dấu; thiếu glyph thì gán font TMP có dấu làm Default Font Asset rồi Reimport. "
            "Cards/Fronts/ là ảnh thẻ đã in sẵn chữ (dùng ngay không cần prefab), Cards/card_back.png là mặt sau chung "
            f"({src.asset}_back.prefab). "
            f"{src.asset}_CardsDemo.prefab: Canvas mẫu bày vài thẻ + mặt sau để xem nhanh. "
            f"Cards/{src.asset}_cards.asset: ScriptableObject AssetStudio.CardSetData (Scripts/CardSetData.cs) — "
            "cards[i].front / art / prefab, Find(id). Cards/Atlas/: atlas mặt trước + mặt sau.")
    return files, [note, *[f"  {ln}" for ln in atlas_notes]]


def _gs(s: Any) -> str:
    return '"' + str(s or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _gn(v: float) -> str:
    s = f"{float(v):.6g}"
    return s if any(ch in s for ch in ".e") else f"{s}.0"


def _color(h: str) -> str:
    r, g, b = _rgb(h)
    return f"Color({_gn(r)}, {_gn(g)}, {_gn(b)}, 1)"


def godot_scene(src: Source, card: dict[str, Any]) -> str:
    doc = src.cards or {}
    w, h = doc.get("size") or (756, 1056)
    fonts = doc.get("fonts") or {}
    ext = [f'[ext_resource type="Texture2D" path="{rel(src, "art:" + card["id"])}" id="1_art"]',
           f'[ext_resource type="Texture2D" path="{rel(src, "frame:" + card["frame"])}" id="2_frame"]']
    subs: list[str] = []
    for role in ("title", "body", "number"):
        f = fonts.get(role) or {}
        names = ", ".join(_gs(n) for n in dict.fromkeys([f.get("family") or "", *FALLBACK_FONTS]) if n)
        subs += [f'[sub_resource type="SystemFont" id="SystemFont_{role}"]', f"font_names = PackedStringArray({names})",
                 f"font_weight = {900 if 'black' in str(f.get('style', '')).lower() else (700 if f.get('bold') else 400)}",
                 ""]

    def anchors(rect: list[float]) -> list[str]:
        x, y, rw, rh = rect
        return [f"anchor_left = {_gn(x)}", f"anchor_top = {_gn(y)}", f"anchor_right = {_gn(x + rw)}",
                f"anchor_bottom = {_gn(y + rh)}", "grow_horizontal = 2", "grow_vertical = 2"]

    nodes = [f'[node name="Card_{card["id"]}" type="Control"]', f"custom_minimum_size = Vector2({w}, {h})",
             "layout_mode = 3", f"offset_right = {_gn(w)}", f"offset_bottom = {_gn(h)}", "",
             '[node name="Art" type="TextureRect" parent="."]', "layout_mode = 1", *anchors(card["art_rect"]),
             'texture = ExtResource("1_art")', "expand_mode = 1", "stretch_mode = 6", "",
             '[node name="Frame" type="TextureRect" parent="."]', "layout_mode = 1", *anchors([0, 0, 1, 1]),
             'texture = ExtResource("2_frame")', "expand_mode = 1", ""]
    for role in ROLES:
        t = (card.get("texts") or {}).get(role)
        if not t:
            continue
        font = {"name": "title", "desc": "body", "type": "body"}.get(role, "number")
        nodes += [f'[node name="{NODE[role]}" type="Label" parent="."]', "layout_mode = 1", *anchors(t["rect"]),
                  f"theme_override_colors/font_color = {_color(str(t.get('color') or '#ffffff'))}"]
        if t.get("stroke"):
            nodes += [f"theme_override_colors/font_outline_color = {_color(str(t.get('stroke_color') or '#000000'))}",
                      f"theme_override_constants/outline_size = {int(t['stroke']) * 2}"]
        nodes += [f'theme_override_fonts/font = SubResource("SystemFont_{font}")',
                  f"theme_override_font_sizes/font_size = {int(t.get('size') or 24)}", f"text = {_gs(t['text'])}",
                  "horizontal_alignment = 1", "vertical_alignment = 1"]
        if t.get("wrap"):
            nodes.append("autowrap_mode = 3")
        nodes.append("")
    return "\n".join([f"[gd_scene load_steps={len(ext) + 4} format=3]", "", *ext, "", *subs, *nodes])


GODOT_SCRIPT_FILE = "card_set_data.gd"
GODOT_SCRIPT = """extends Resource
## Card set from AI Asset Studio: each card is a Dictionary (id, name, description, cost, attack, health — -1 when the
## card has none — rarity, rarity_index, type, type_label, flavor, front / art Texture2D, scene PackedScene).

@export var card_size := Vector2(756, 1056)
@export var back: Texture2D
@export var cards: Array = []


func find(id: String) -> Dictionary:
	for c in cards:
		if c["id"] == id:
			return c
	return {}


func instantiate(id: String) -> Control:
	var c := find(id)
	return null if c.is_empty() else (c["scene"] as PackedScene).instantiate()
"""


def godot_tres(src: Source) -> str:
    doc = src.cards or {}
    w, h = doc.get("size") or (756, 1056)
    ext = [f'[ext_resource type="Script" path="{GODOT_SCRIPT_FILE}" id="1_script"]',
           f'[ext_resource type="Texture2D" path="{rel(src, "back")}" id="2_back"]']
    rows = []
    for i, c in enumerate(doc.get("cards") or []):
        ext += [f'[ext_resource type="Texture2D" path="{rel(src, "front:" + c["id"])}" id="f{i}"]',
                f'[ext_resource type="Texture2D" path="{rel(src, "art:" + c["id"])}" id="a{i}"]',
                f'[ext_resource type="PackedScene" path="{scene_file(src, c)}" id="s{i}"]']
        num = {k: (-1 if c.get(k) is None else int(c[k])) for k in ("cost", "attack", "health")}
        rows.append(f'{{"id": {_gs(c["id"])}, "name": {_gs(c["name"])}, "description": {_gs(c["description"])}, '
                    f'"cost": {num["cost"]}, "attack": {num["attack"]}, "health": {num["health"]}, '
                    f'"rarity": {_gs(c["rarity"])}, "rarity_index": {int(c["rarity_index"])}, "type": {_gs(c["type"])}, '
                    f'"type_label": {_gs(c.get("type_label"))}, "flavor": {_gs(c.get("flavor"))}, '
                    f'"front": ExtResource("f{i}"), "art": ExtResource("a{i}"), "scene": ExtResource("s{i}")}}')
    return "\n".join([f'[gd_resource type="Resource" load_steps={len(ext) + 1} format=3]', "", *ext, "",
                      "[resource]", 'script = ExtResource("1_script")', f"card_size = Vector2({w}, {h})",
                      'back = ExtResource("2_back")', "cards = [" + ", ".join(rows) + "]", ""])


def scene_file(src: Source, card: dict[str, Any]) -> str:
    return f"{src.asset}_{card['id']}.tscn"


def godot_files(src: Source) -> tuple[Files, list[str]]:
    files: Files = [(rel(src, k), p) for k, p in src.cards_files.items()]
    cards = (src.cards or {}).get("cards") or []
    files += [(scene_file(src, c), godot_scene(src, c).encode("utf-8")) for c in cards]
    files += [(f"Cards/{src.asset}_cards.json", index_json(src)), (GODOT_SCRIPT_FILE, GODOT_SCRIPT.encode()),
              (f"{src.asset}_cards.tres", godot_tres(src).encode("utf-8"))]
    atlas_files, atlas_notes = _atlas(src, "godot")
    files += atlas_files
    note = (f"- {src.asset}_<id>.tscn: {len(cards)} thẻ Control (TextureRect Art dưới Frame có ô trong suốt, Label tên / "
            "dòng loại / luật / chi phí / công / máu với SystemFont cùng họ font ảnh in sẵn, có fallback chữ Việt; "
            "không dùng font bitmap vì cỡ chữ mỗi dòng mỗi thẻ khác nhau sau khi tự co, font bitmap chỉ đúng một cỡ). "
            f"{src.asset}_cards.tres: Resource ({GODOT_SCRIPT_FILE}) — cards[i] là Dictionary (front, art, scene); "
            "dùng load(\".../" + f"{src.asset}_cards.tres\").instantiate(\"<id>\"). Cards/Fronts/ là ảnh thẻ đã in "
            "chữ, Cards/card_back.png mặt sau chung, Cards/Atlas/ atlas (AtlasTexture .tres).")
    return files, [note, *[f"  {ln}" for ln in atlas_notes]]
