extends SceneTree

func _initialize() -> void:
	var scene: PackedScene = load("res://Hero/Hero_paperdoll.tscn")
	var doll = scene.instantiate()
	root.add_child(doll)
	await process_frame
	print("root anim=", doll.animation, " has face=", doll.has_method("face"), " layers=", doll._layers.size())
	doll.play("attack_W")
	doll.frame = 2
	await process_frame
	for c in doll.get_children():
		if c is AnimatedSprite2D:
			print(c.name, " anim=", c.animation, " frame=", c.frame, " z=", c.z_index)
	var m: Marker2D = doll.socket("hand_r")
	print("hand_r pos=", m.position, " rot=", m.rotation_degrees)
	doll.face("walk", Vector2(1, 0))
	print("after face anim=", doll.animation)
	quit()
