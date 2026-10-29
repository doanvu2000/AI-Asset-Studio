@tool
extends EditorContextMenuPlugin
## FileSystem dock right-click → AI Asset Studio actions, each opening the dock on a prefilled recipe.

const MESH_EXT := ["glb", "gltf", "fbx", "obj"]
const IMAGE_EXT := ["png", "jpg", "jpeg", "webp"]

var dock: Node


func _popup_menu(paths: PackedStringArray) -> void:
	if paths.size() != 1:
		return
	var p := paths[0]
	var ext := p.get_extension().to_lower()
	add_context_menu_item("AI Asset Studio: Restyle", _restyle)
	if ext in MESH_EXT:
		add_context_menu_item("AI Asset Studio: Tô lại texture", _retexture)
	if ext in IMAGE_EXT:
		add_context_menu_item("AI Asset Studio: Biến thể kẻ địch", _enemy_variants)
	if ext != "":
		add_context_menu_item("AI Asset Studio: Thêm vào Dự án", _add_to_project)


func _abs(p: String) -> String:
	return ProjectSettings.globalize_path(p)


func _restyle(paths: Array) -> void:
	var p: String = paths[0]
	var folder := p if DirAccess.dir_exists_absolute(_abs(p)) else p.get_base_dir()
	dock.open_recipe("restyle-folder", {"folder": _abs(folder)})


func _retexture(paths: Array) -> void:
	dock.open_recipe("mesh-retexture", {"mesh": _abs(paths[0])})


## The sheet's .json (same stem) rides along when it sits next to the PNG.
func _enemy_variants(paths: Array) -> void:
	var png := _abs(paths[0])
	var prefill := {"sheet": png}
	var json := png.get_basename() + ".json"
	if FileAccess.file_exists(json):
		prefill["meta"] = json
	dock.open_recipe("enemy-variants", prefill)


func _add_to_project(paths: Array) -> void:
	dock.open_add_to_project(paths[0])
