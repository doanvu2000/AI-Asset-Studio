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
	if res is PackedScene and ClassDB.is_parent_class(res.get_state().get_node_type(0), "Control"):
		await _ui_preview(res, out)
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
	var particles := node.find_children("*", "GPUParticles3D", true, false)
	if node is GPUParticles3D:
		particles.append(node)
	if particles.is_empty():
		for i in frames:
			await process_frame
	else:
		await _particle_shot(particles)
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
	if res is TileSet:
		return _tile_demo(res)
	return null


# A TileSet with no scene of its own (animated tile) previews as a small painted TileMapLayer.
func _tile_demo(ts: TileSet) -> Node:
	if ts.get_source_count() == 0:
		return null
	var sid := ts.get_source_id(0)
	var src := ts.get_source(sid) as TileSetAtlasSource
	if src == null or src.get_tiles_count() == 0:
		return null
	var layer := TileMapLayer.new()
	layer.tile_set = ts
	var n := src.get_tiles_count()
	var side := 4 if n == 1 else ceili(sqrt(float(n)))
	for i in side * side:
		layer.set_cell(Vector2i(i % side, floori(float(i) / side)), sid, src.get_tile_id(i % n))
	return layer


func _aabbs(n: Node, acc: Array) -> void:
	if n is GPUParticles3D and n.is_visible_in_tree():
		# GPUParticles3D.get_aabb() is always empty: its culling box is visibility_aabb.
		if n.visibility_aabb.size.length() > 0.0:
			acc.append(n.global_transform * n.visibility_aabb)
	elif n is VisualInstance3D and n.is_visible_in_tree():
		var a: AABB = n.get_aabb()
		if a.size.length() > 0.0:
			acc.append(n.global_transform * a)
	elif n is GridMap and n.mesh_library and n.is_visible_in_tree():
		# GridMap draws through the RenderingServer, so it is no VisualInstance3D: frame its cells' meshes.
		for cell in n.get_used_cells():
			var item: int = n.get_cell_item(cell)
			var mesh: Mesh = n.mesh_library.get_item_mesh(item)
			if mesh:
				var t := Transform3D(n.get_cell_item_basis(cell), n.map_to_local(cell))
				acc.append(n.global_transform * t * n.mesh_library.get_item_mesh_transform(item) * mesh.get_aabb())
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


var _cam3d: Camera3D
var _focus := 1.0


# Particle FX: a fixed frame count catches a burst as a dot or after a short hit spark has died, so the shot waits
# for ~30% of each one-shot layer's life (after its delay). The visibility box is a loose cube and capture_aabb() is
# no tighter under gl_compatibility, so the camera then zooms onto the pixels that differ from the background.
func _particle_shot(particles: Array) -> void:
	var sum := 0.0
	var n := 0
	for p: GPUParticles3D in particles:
		if p.one_shot or p.has_meta("delay"):
			sum += float(p.get_meta("delay", 0.0)) + p.lifetime * 0.3
			n += 1
	var wait_ms := clampi(int(1000.0 * sum / maxi(n, 1)), 100, 1500)
	var started := Time.get_ticks_msec()
	var frames := 0
	while frames < 3 or Time.get_ticks_msec() - started < wait_ms:
		await process_frame
		frames += 1
	var img := get_root().get_texture().get_image()
	img.convert(Image.FORMAT_RGB8)
	var used := _content_rect(img)
	var half := maxf(used.size.x, used.size.y) * 0.5 * 1.4
	if used.size.x <= 0 or half > img.get_width() * 0.4:
		return
	var w := float(img.get_width())
	var c := used.get_center() - Vector2(w, img.get_height()) * 0.5
	var t := tan(deg_to_rad(_cam3d.fov * 0.5))
	var k := maxf(half / (w * 0.5), 0.05)
	_cam3d.h_offset = c.x / (w * 0.5) * _focus * t
	_cam3d.v_offset = -c.y / (w * 0.5) * _focus * t
	_cam3d.fov = rad_to_deg(2.0 * atan(t * k))
	for i in 2:
		await process_frame


func _content_rect(img: Image) -> Rect2:
	var bg := BG
	var lo := Vector2(INF, INF)
	var hi := Vector2(-INF, -INF)
	var step := 2
	for y in range(0, img.get_height(), step):
		for x in range(0, img.get_width(), step):
			var p := img.get_pixel(x, y)
			if absf(p.r - bg.r) + absf(p.g - bg.g) + absf(p.b - bg.b) > 0.08:
				lo = Vector2(minf(lo.x, x), minf(lo.y, y))
				hi = Vector2(maxf(hi.x, x), maxf(hi.y, y))
	if lo.x > hi.x:
		return Rect2()
	return Rect2(lo, hi - lo + Vector2(step, step))


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
	_focus = radius / sin(deg_to_rad(cam.fov * 0.5)) * 1.05
	cam.position = center + dir * _focus
	get_root().add_child(cam)
	cam.look_at(center)
	cam.make_current()
	_cam3d = cam
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


# UI screens: one SubViewport per phone shape (canvas_items + expand logic: the short side keeps the base size),
# 20:9 with a notch / home-bar safe area; every Control must stay inside SafeArea and its parent.
const UI_ASPECTS := [[9.0, 16.0, 0.0, 0.0], [9.0, 20.0, 0.04, 0.0267], [3.0, 4.0, 0.0, 0.0]]


func _ui_preview(scene: PackedScene, out: String) -> void:
	var probe: Control = scene.instantiate()
	var base: Vector2 = probe.get_meta("asset_studio_ref", Vector2(1080, 1920))
	probe.free()
	var landscape := base.x > base.y
	var shots: Array[Image] = []
	var bad := 0
	for a in UI_ASPECTS:
		var ax: float = a[1] if landscape else a[0]
		var ay: float = a[0] if landscape else a[1]
		var size := Vector2(base.x, base.x * ay / ax) if not landscape else Vector2(base.y * ax / ay, base.y)
		if size.x < base.x or size.y < base.y:
			size = Vector2(base.y * ax / ay, base.y) if not landscape else Vector2(base.x, base.x * ay / ax)
		var vp := SubViewport.new()
		vp.size = Vector2i(size)
		vp.transparent_bg = false
		vp.render_target_update_mode = SubViewport.UPDATE_ALWAYS
		get_root().add_child(vp)
		var bg := ColorRect.new()
		bg.color = BG
		bg.set_anchors_preset(Control.PRESET_FULL_RECT)
		vp.add_child(bg)
		var ui: Control = scene.instantiate()
		vp.add_child(ui)
		var safe := ui.get_node_or_null("SafeArea") as Control
		if safe != null:
			var notch: float = a[2]
			var bar: float = a[3]
			if landscape:
				safe.anchor_left = notch
				safe.anchor_right = 1.0 - notch
				safe.anchor_bottom = 1.0 - bar
			else:
				safe.anchor_top = notch
				safe.anchor_bottom = 1.0 - bar
		for i in 4:
			await process_frame
		var label := "%dx%d" % [int(a[0]), int(a[1])]
		bad += _ui_check(safe if safe != null else ui, label)
		var img := vp.get_texture().get_image()
		img.resize(int(round(img.get_width() * 960.0 / img.get_height())), 960, Image.INTERPOLATE_BILINEAR)
		shots.append(img)
		vp.queue_free()
	var gap := 16
	var w := gap
	for im in shots:
		w += im.get_width() + gap
	var sheet := Image.create(w, 960 + 2 * gap, false, Image.FORMAT_RGBA8)
	sheet.fill(BG)
	var x := gap
	for im in shots:
		im.convert(Image.FORMAT_RGBA8)
		sheet.blit_rect(im, Rect2i(Vector2i.ZERO, im.get_size()), Vector2i(x, gap))
		x += im.get_width() + gap
	var err := sheet.save_png(out)
	if err != OK:
		printerr("PREVIEW_FAIL save_png ", err)
		quit(5)
		return
	print("PREVIEW_OK ui ", shots.size(), " aspects, overflow ", bad)
	quit(0)


func _ui_check(safe: Control, label: String) -> int:
	var sr := safe.get_global_rect()
	var bad := 0
	for c in safe.find_children("*", "Control", true, false):
		var r: Rect2 = c.get_global_rect()
		var p := c.get_parent() as Control
		var pr: Rect2 = p.get_global_rect() if p != null else sr
		var lo := Vector2(maxf(sr.position.x, pr.position.x), maxf(sr.position.y, pr.position.y)) - Vector2.ONE
		var hi := Vector2(minf(sr.end.x, pr.end.x), minf(sr.end.y, pr.end.y)) + Vector2.ONE
		if r.position.x < lo.x or r.position.y < lo.y or r.end.x > hi.x or r.end.y > hi.y:
			printerr("PREVIEW_FAIL UI overflow ", label, ": ", c.name)
			bad += 1
	return bad
