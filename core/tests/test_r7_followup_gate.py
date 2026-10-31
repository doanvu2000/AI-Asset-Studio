from studio_core.mcp.followup import follow_ups, looks_like_level

MINIMAP = {"id": "minimap", "name": "Minimap", "inputs": {
    "source": {"type": "file", "ext": ["png", "jpg", "tmx", "tmj", "ldtk"]}}}


def _png(name: str) -> dict:
    return {"hash": "p" * 64, "name": name, "mime": "image/png"}


def test_minimap_hidden_for_plain_png() -> None:
    assert follow_ups([MINIMAP], _png("hero.png"), from_recipe="concept-2d") == []
    assert follow_ups([MINIMAP], _png("hero_normal_map.png")) == []


def test_minimap_offered_for_levels() -> None:
    assert [f["recipe"] for f in follow_ups([MINIMAP], _png("level_01.png"))] == ["minimap"]
    assert follow_ups([MINIMAP], _png("anything.png"), from_recipe="tileset-seamless")
    tmx = {"hash": "t" * 64, "name": "world.tmx", "mime": "application/xml"}
    assert follow_ups([MINIMAP], tmx)


def test_looks_like_level_word_boundaries() -> None:
    assert looks_like_level(_png("forest-map.png"))
    assert looks_like_level(_png("Dungeon Room 3.webp"))
    assert not looks_like_level(_png("mapper.png"))
    assert not looks_like_level(_png("roughness_map.png"))
    assert not looks_like_level({"hash": "x", "name": "level.glb", "mime": "model/gltf-binary"})
