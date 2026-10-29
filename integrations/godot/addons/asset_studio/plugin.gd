@tool
extends EditorPlugin

const Dock := preload("res://addons/asset_studio/dock.gd")
const ContextMenu := preload("res://addons/asset_studio/context_menu.gd")

var dock: Control
var menu: EditorContextMenuPlugin


func _enter_tree() -> void:
	dock = Dock.new()
	add_control_to_dock(DOCK_SLOT_RIGHT_UL, dock)
	menu = ContextMenu.new()
	menu.dock = dock
	add_context_menu_plugin(EditorContextMenuPlugin.CONTEXT_SLOT_FILESYSTEM, menu)


func _exit_tree() -> void:
	if menu:
		remove_context_menu_plugin(menu)
		menu = null
	if dock:
		remove_control_from_docks(dock)
		dock.queue_free()
		dock = null
