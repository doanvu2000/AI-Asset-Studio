extends Node3D

@export var toon_material: ShaderMaterial


func _ready() -> void:
	_apply(self)


func _apply(node: Node) -> void:
	if node is MeshInstance3D and node.mesh != null:
		for i in node.mesh.get_surface_count():
			node.set_surface_override_material(i, _toon_for(node.get_active_material(i)))
	for child in node.get_children():
		_apply(child)


# material_override would drop each surface's own texture, so the toon material is cloned per surface with it.
func _toon_for(src: Material) -> ShaderMaterial:
	var m := toon_material.duplicate() as ShaderMaterial
	if src is BaseMaterial3D:
		m.set_shader_parameter("albedo_color", src.albedo_color)
		if src.albedo_texture != null:
			m.set_shader_parameter("albedo_tex", src.albedo_texture)
	return m
