extends SceneTree

const BG := Color(0.17, 0.17, 0.19)


func _init() -> void:
	var args := OS.get_cmdline_user_args()
	if args.size() < 2:
		printerr("PREVIEW_FAIL usage: -- <res path> <out.png> [frames]")
		quit(2)
		return
	var target: String = args[0]
	var out: String = args[1]
	var frames := int(args[2]) if args.size() > 2 else 12
	RenderingServer.set_default_clear_color(BG)
	var res = load(target)
	if res == null:
		printerr("PREVIEW_FAIL cannot load ", target)
		quit(3)
		return
	var node := _spawn(res)
	if node == null:
		printerr("PREVIEW_FAIL unsupported resource ", res.get_class())
		quit(4)
		return
	get_root().add_child(node)
	await process_frame
	var boxes: Array = []
	_aabbs(node, boxes)
	if boxes.is_empty():
		_frame_2d(node)
	else:
		_frame_3d(node, boxes)
	for i in frames:
		await process_frame
	var img := get_root().get_texture().get_image()
	var err := img.save_png(out)
	if err != OK:
		printerr("PREVIEW_FAIL save_png ", err)
		quit(5)
		return
	print("PREVIEW_OK ", node.get_class(), " ", img.get_width(), "x", img.get_height())
	quit(0)


func _spawn(res: Resource) -> Node:
	if res is PackedScene:
		return res.instantiate()
	if res is SpriteFrames:
		var a := AnimatedSprite2D.new()
		a.sprite_frames = res
		var names: PackedStringArray = res.get_animation_names()
		if names.size() > 0:
			a.play(names[0])
		return a
	if res is Texture2D:
		var s := Sprite2D.new()
		s.texture = res
		return s
	if res is Mesh:
		var m := MeshInstance3D.new()
		m.mesh = res
		return m
	return null


func _aabbs(n: Node, acc: Array) -> void:
	if n is VisualInstance3D and n.is_visible_in_tree():
		var a: AABB = n.get_aabb()
		if a.size.length() > 0.0:
			acc.append(n.global_transform * a)
	for c in n.get_children():
		_aabbs(c, acc)


func _rects(n: Node, acc: Array) -> void:
	if n is Sprite2D and n.texture:
		acc.append(n.get_global_transform() * n.get_rect())
	elif n is AnimatedSprite2D and n.sprite_frames:
		var tex: Texture2D = n.sprite_frames.get_frame_texture(n.animation, n.frame)
		if tex:
			var sz := tex.get_size()
			var pos: Vector2 = n.offset - (sz / 2.0 if n.centered else Vector2.ZERO)
			acc.append(n.get_global_transform() * Rect2(pos, sz))
	elif n is TileMapLayer and n.tile_set:
		var r: Rect2i = n.get_used_rect()
		var ts := Vector2(n.tile_set.tile_size)
		acc.append(n.get_global_transform() * Rect2(Vector2(r.position) * ts, Vector2(r.size) * ts))
	elif n is Control:
		acc.append(n.get_global_rect())
	for c in n.get_children():
		_rects(c, acc)


func _frame_2d(node: Node) -> void:
	var rects: Array = []
	_rects(node, rects)
	if rects.is_empty():
		return
	var r: Rect2 = rects[0]
	for x in rects:
		r = r.merge(x)
	var vp := Vector2(get_root().size)
	var cam := Camera2D.new()
	cam.position = r.get_center()
	var z := 0.9 * minf(vp.x / maxf(r.size.x, 1.0), vp.y / maxf(r.size.y, 1.0))
	cam.zoom = Vector2(z, z)
	get_root().add_child(cam)
	cam.make_current()


func _frame_3d(node: Node, boxes: Array) -> void:
	var box: AABB = boxes[0]
	for b in boxes:
		box = box.merge(b)
	var center := box.get_center()
	var radius := maxf(box.size.length() * 0.5, 0.01)
	var cam := Camera3D.new()
	cam.fov = 35.0
	cam.near = radius * 0.01
	cam.far = radius * 100.0
	var dir := Vector3(0.8, 0.55, 1.0).normalized()
	cam.position = center + dir * (radius / sin(deg_to_rad(cam.fov * 0.5)) * 1.05)
	get_root().add_child(cam)
	cam.look_at(center)
	cam.make_current()
	if node.find_children("*", "Light3D", true, false).is_empty():
		var sun := DirectionalLight3D.new()
		sun.rotation_degrees = Vector3(-50, 35, 0)
		get_root().add_child(sun)
	if node.find_children("*", "WorldEnvironment", true, false).is_empty():
		var env := Environment.new()
		env.background_mode = Environment.BG_COLOR
		env.background_color = BG
		env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
		env.ambient_light_color = Color(0.55, 0.55, 0.6)
		var we := WorldEnvironment.new()
		we.environment = env
		get_root().add_child(we)
