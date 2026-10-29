@tool
extends VBoxContainer

const StudioClient := preload("res://addons/asset_studio/studio_client.gd")
const POLL_S := 1.0
const THUMB := 96

var client: Node
var recipes: Array = []
var projects: Array = []
var recipe_id := ""
var project_id := ""
var run_id := ""
var run: Dictionary = {}
var steps: Array = []
var values := {}
var picked := {}
var thumbs := {}
var busy := false
var imported_run := ""
var add_to_project_path := ""
var _pending_prefill := {}
var _gate_sig := ""

var _status: Label
var _error: Label
var _offline: VBoxContainer
var _main: VBoxContainer
var _recipe_opt: OptionButton
var _project_opt: OptionButton
var _desc: Label
var _title: LineEdit
var _form: GridContainer
var _auto_import: CheckBox
var _run_btn: Button
var _add_box: VBoxContainer
var _add_label: Label
var _style_btn: Button
var _run_box: VBoxContainer
var _run_label: Label
var _progress: ProgressBar
var _steps_label: Label
var _gates: VBoxContainer
var _imported: VBoxContainer
var _timer: Timer


func _ready() -> void:
	name = "AI Asset Studio"
	client = StudioClient.new()
	add_child(client)
	_build_ui()
	_timer = Timer.new()
	_timer.wait_time = POLL_S
	_timer.timeout.connect(_poll)
	add_child(_timer)
	_timer.start()
	reconnect()


func _build_ui() -> void:
	var head := HBoxContainer.new()
	var t := Label.new()
	t.text = "AI Asset Studio"
	t.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	head.add_child(t)
	var rc := Button.new()
	rc.text = "Kết nối lại"
	rc.pressed.connect(reconnect)
	head.add_child(rc)
	add_child(head)
	_status = _label(true)
	_error = _label(true)
	_error.add_theme_color_override("font_color", Color(1, 0.45, 0.4))
	_offline = VBoxContainer.new()
	var off_msg := _label(true, _offline)
	off_msg.text = "Không thấy AI Asset Studio đang chạy trên máy này. Mở app rồi bấm Kết nối lại."
	var open_btn := Button.new()
	open_btn.text = "Mở AI Asset Studio"
	open_btn.pressed.connect(_open_app)
	_offline.add_child(open_btn)
	add_child(_offline)
	_main = VBoxContainer.new()
	add_child(_main)
	_add_box = VBoxContainer.new()
	_main.add_child(_add_box)
	_add_label = _label(true, _add_box)
	_project_opt = OptionButton.new()
	_project_opt.item_selected.connect(func(i): project_id = str(_project_opt.get_item_metadata(i)))
	_main.add_child(_row("Dự án", _project_opt))
	var add_hint := _label(true, _add_box)
	add_hint.text = ("Nhập vào Thư viện: file thành asset của Dự án (duyệt, tìm tương tự, đóng gói). "
		+ "Ảnh style: ảnh này thành tham chiếu style, các quy trình chạy trong Dự án bám theo nó.")
	var add_row := HBoxContainer.new()
	var add_btn := Button.new()
	add_btn.text = "Nhập vào Thư viện"
	add_btn.pressed.connect(_add_to_library)
	_style_btn = Button.new()
	_style_btn.text = "Đặt làm ảnh style"
	_style_btn.pressed.connect(_set_style_reference)
	var cancel := Button.new()
	cancel.text = "Huỷ"
	cancel.pressed.connect(_cancel_add)
	add_row.add_child(add_btn)
	add_row.add_child(_style_btn)
	add_row.add_child(cancel)
	_add_box.add_child(add_row)
	_recipe_opt = OptionButton.new()
	_recipe_opt.item_selected.connect(_on_recipe_selected)
	_main.add_child(_row("Quy trình", _recipe_opt))
	_desc = _label(true, _main)
	_title = LineEdit.new()
	_main.add_child(_row("Tiêu đề", _title))
	_form = GridContainer.new()
	_form.columns = 2
	_main.add_child(_form)
	_auto_import = CheckBox.new()
	_auto_import.text = "Tự nhận output vào res://asset_studio"
	_auto_import.button_pressed = true
	_main.add_child(_auto_import)
	_run_btn = Button.new()
	_run_btn.text = "Chạy"
	_run_btn.pressed.connect(_start)
	_main.add_child(_run_btn)
	_run_box = VBoxContainer.new()
	_main.add_child(_run_box)
	_run_label = _label(false, _run_box)
	_progress = ProgressBar.new()
	_progress.max_value = 1.0
	_run_box.add_child(_progress)
	_steps_label = _label(true, _run_box)
	_gates = VBoxContainer.new()
	_run_box.add_child(_gates)
	var run_row := HBoxContainer.new()
	var cancel_run := Button.new()
	cancel_run.text = "Huỷ run"
	cancel_run.pressed.connect(func(): _act(func(): return await client.request("POST", "/api/runs/%s/cancel" % run_id)))
	var import_btn := Button.new()
	import_btn.text = "Nhận output"
	import_btn.pressed.connect(func(): _import())
	run_row.add_child(cancel_run)
	run_row.add_child(import_btn)
	_run_box.add_child(run_row)
	_imported = VBoxContainer.new()
	_run_box.add_child(_imported)


func _label(wrap: bool, parent: Node = self) -> Label:
	var l := Label.new()
	if wrap:
		l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		l.custom_minimum_size.x = 200
	parent.add_child(l)
	return l


func _row(caption: String, field: Control) -> HBoxContainer:
	var h := HBoxContainer.new()
	var l := Label.new()
	l.text = caption
	l.custom_minimum_size.x = 80
	h.add_child(l)
	field.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	h.add_child(field)
	return h


func _cancel_add() -> void:
	add_to_project_path = ""
	_refresh()


func _set_error(msg: String) -> void:
	_error.text = msg
	_error.visible = msg != ""


func reconnect() -> void:
	_status.text = "Đang tìm AI Asset Studio…"
	_set_error("")
	var ok: bool = await client.connect_core()
	if ok:
		var r: Dictionary = await client.request("GET", "/api/recipes")
		var p: Dictionary = await client.request("GET", "/api/projects")
		recipes = r.data if r.ok and r.data is Array else []
		projects = p.data if p.ok and p.data is Array else []
		_status.text = "Đã kết nối (%d quy trình)" % recipes.size()
		if not r.ok:
			_set_error(r.error)
	else:
		_status.text = ""
	_fill_options()
	_refresh()


func _fill_options() -> void:
	_recipe_opt.clear()
	for r in recipes:
		_recipe_opt.add_item("%s · %s" % [r.get("name", ""), r.get("id", "")])
		_recipe_opt.set_item_metadata(_recipe_opt.item_count - 1, r.get("id", ""))
	_project_opt.clear()
	_project_opt.add_item("(không có)")
	_project_opt.set_item_metadata(0, "")
	for p in projects:
		_project_opt.add_item(str(p.get("name", "")))
		_project_opt.set_item_metadata(_project_opt.item_count - 1, str(p.get("id", "")))
	for i in _project_opt.item_count:
		if str(_project_opt.get_item_metadata(i)) == project_id:
			_project_opt.select(i)
	_select_recipe(recipe_id if recipe_id != "" else (str(recipes[0].get("id")) if recipes.size() > 0 else ""))


func _recipe() -> Dictionary:
	for r in recipes:
		if str(r.get("id")) == recipe_id:
			return r
	return {}


func _on_recipe_selected(i: int) -> void:
	values.clear()
	_select_recipe(str(_recipe_opt.get_item_metadata(i)))


## Opens the dock on a recipe with some inputs filled (context-menu entry points).
func open_recipe(id: String, prefill: Dictionary) -> void:
	add_to_project_path = ""
	values.clear()
	_pending_prefill = prefill
	_select_recipe(id)
	_refresh()


func open_add_to_project(path: String) -> void:
	add_to_project_path = path
	_refresh()


func _select_recipe(id: String) -> void:
	recipe_id = id
	for i in _recipe_opt.item_count:
		if str(_recipe_opt.get_item_metadata(i)) == id:
			_recipe_opt.select(i)
	var r := _recipe()
	if r.is_empty():
		return
	var specs: Dictionary = r.get("inputs", {})
	for k in specs:
		if not values.has(k):
			values[k] = _default_text(specs[k])
	for k in _pending_prefill:
		values[k] = _pending_prefill[k]
	_pending_prefill = {}
	_desc.text = str(r.get("description", ""))
	_build_form(specs)


static func _default_text(spec: Dictionary) -> String:
	var d = spec.get("default")
	if d == null:
		return ""
	if d is bool:
		return "true" if d else "false"
	if d is float and spec.get("type") == "int":
		return str(int(d))
	return str(d)


static func _required(spec: Dictionary) -> bool:
	return bool(spec.get("required", true)) if spec.has("required") else not spec.has("default")


func _build_form(specs: Dictionary) -> void:
	for c in _form.get_children():
		c.queue_free()
	for key in specs:
		var spec: Dictionary = specs[key]
		var lab := Label.new()
		var text := str(spec.get("label", key))
		lab.text = (text.left(40) + "…" if text.length() > 42 else text) + (" *" if _required(spec) else "")
		lab.tooltip_text = text
		_form.add_child(lab)
		_form.add_child(_field(key, spec))


func _field(key: String, spec: Dictionary) -> Control:
	var v := str(values.get(key, ""))
	var type := str(spec.get("type", "text"))
	var options = spec.get("options")
	if options is Array and options.size() > 0:
		var ob := OptionButton.new()
		for o in options:
			var s := str(int(o)) if (o is float and type == "int") else str(o)
			ob.add_item(s)
			if s == v:
				ob.select(ob.item_count - 1)
		if ob.selected < 0:
			ob.select(0)
			values[key] = ob.get_item_text(0)
		ob.item_selected.connect(func(i): values[key] = ob.get_item_text(i))
		ob.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		return ob
	if type == "bool":
		var cb := CheckBox.new()
		cb.button_pressed = v == "true"
		cb.toggled.connect(func(on): values[key] = "true" if on else "false")
		return cb
	if type in StudioClient.BLOB_TYPES or str(spec.get("path", "")) == "dir":
		var h := HBoxContainer.new()
		h.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		var le := LineEdit.new()
		le.text = v
		le.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		le.text_changed.connect(func(t): values[key] = t)
		h.add_child(le)
		var b := Button.new()
		b.text = "…"
		b.pressed.connect(func(): _browse(key, spec, le))
		h.add_child(b)
		return h
	if bool(spec.get("multiline", false)):
		var te := TextEdit.new()
		te.text = v
		te.custom_minimum_size.y = 54
		te.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		te.text_changed.connect(func(): values[key] = te.text)
		return te
	var line := LineEdit.new()
	line.text = v
	line.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	line.text_changed.connect(func(t): values[key] = t)
	return line


func _browse(key: String, spec: Dictionary, le: LineEdit) -> void:
	var fd := EditorFileDialog.new()
	fd.access = EditorFileDialog.ACCESS_FILESYSTEM
	fd.file_mode = EditorFileDialog.FILE_MODE_OPEN_DIR if str(spec.get("path", "")) == "dir" else EditorFileDialog.FILE_MODE_OPEN_FILE
	var exts = spec.get("ext")
	if exts is Array:
		for e in exts:
			fd.add_filter("*." + str(e))
	var chosen := func(p: String) -> void:
		le.text = p
		values[key] = p
		fd.queue_free()
	fd.file_selected.connect(chosen)
	fd.dir_selected.connect(chosen)
	fd.canceled.connect(fd.queue_free)
	EditorInterface.get_base_control().add_child(fd)
	fd.popup_file_dialog()


func _start() -> void:
	if busy:
		return
	var r := _recipe()
	if r.is_empty():
		return
	busy = true
	_set_error("")
	var specs: Dictionary = r.get("inputs", {})
	var inputs := {}
	for k in specs:
		var spec: Dictionary = specs[k]
		var text := str(values.get(k, ""))
		var type := str(spec.get("type", "text"))
		if type in StudioClient.BLOB_TYPES:
			if text.strip_edges() != "":
				inputs[k] = text
			continue
		if text == "" and not _required(spec) and type != "text":
			continue
		match type:
			"int": inputs[k] = int(text)
			"float": inputs[k] = float(text)
			"bool": inputs[k] = text == "true"
			_: inputs[k] = text
	_status.text = "Đang gửi…"
	var res: Dictionary = await client.start_run(r, inputs, project_id, _title.text)
	busy = false
	if not res.ok:
		_set_error(res.error)
		_status.text = ""
		return
	run_id = res.run_id
	run = {}
	steps = []
	picked.clear()
	imported_run = ""
	for c in _imported.get_children():
		c.queue_free()
	_status.text = "Đã bắt đầu"
	_refresh()


func _poll() -> void:
	if busy or run_id == "" or not client.connected():
		return
	var terminal := str(run.get("status", "")) in ["succeeded", "failed", "cancelled", "interrupted"]
	if terminal and (imported_run == run_id or not _auto_import.button_pressed):
		return
	busy = true
	var r: Dictionary = await client.request("GET", "/api/runs/" + run_id)
	busy = false
	if not r.ok:
		_set_error(r.error)
		return
	run = r.data.get("run", {})
	steps = r.data.get("steps", [])
	_refresh()
	if str(run.get("status")) == "succeeded" and _auto_import.button_pressed and imported_run != run_id:
		await _import()


func _import() -> void:
	if run_id == "" or busy:
		return
	busy = true
	imported_run = run_id
	_status.text = "Đang nhận output…"
	var res: Dictionary = await client.import_outputs(run_id)
	busy = false
	if not res.ok:
		_set_error(res.error)
		return
	for c in _imported.get_children():
		c.queue_free()
	for d in res.dirs:
		var b := LinkButton.new()
		b.text = d
		b.pressed.connect(func(): EditorInterface.get_file_system_dock().navigate_to_path(d))
		_imported.add_child(b)
	_status.text = "Đã nhận %d thư mục vào res://asset_studio" % res.dirs.size() if res.dirs.size() > 0 else "Quy trình không có output để nhận"
	EditorInterface.get_resource_filesystem().scan()


func _act(fn: Callable) -> void:
	if busy:
		return
	busy = true
	var r: Dictionary = await fn.call()
	busy = false
	if not r.ok:
		_set_error(r.error)


## The file becomes a Library version of the project; its output is not imported back into res://.
func _add_to_library() -> void:
	if project_id == "" or busy:
		_set_error("Chọn một Dự án trước" if project_id == "" else "")
		return
	var recipe := {}
	for r in recipes:
		if str(r.get("id")) == "library-import":
			recipe = r
	if recipe.is_empty():
		_set_error("Bản AI Asset Studio này chưa có quy trình nhập Thư viện")
		return
	busy = true
	var res: Dictionary = await client.start_run(recipe, {"files": add_to_project_path}, project_id, add_to_project_path.get_file())
	busy = false
	if not res.ok:
		_set_error(res.error)
		return
	run_id = res.run_id
	imported_run = run_id
	run = {}
	steps = []
	_status.text = "Đang nhập %s vào Thư viện" % add_to_project_path.get_file()
	add_to_project_path = ""
	_refresh()


func _set_style_reference() -> void:
	if project_id == "":
		_set_error("Chọn một Dự án trước")
		return
	busy = true
	var up: Dictionary = await client.upload(ProjectSettings.globalize_path(add_to_project_path))
	if up.ok:
		var p: Dictionary = await client.request("GET", "/api/projects/" + project_id)
		if p.ok:
			var style: Dictionary = p.data.get("style", {})
			style["reference_image"] = up.hash
			up = await client.request("PATCH", "/api/projects/" + project_id, {"style": style})
		else:
			up = p
	busy = false
	if not up.ok:
		_set_error(up.error)
		return
	_status.text = "Đã đặt %s làm ảnh style của Dự án" % add_to_project_path.get_file()
	add_to_project_path = ""
	_refresh()


func _open_app() -> void:
	var exe := StudioClient.app_exe()
	if exe == "":
		_set_error("Không tìm thấy AI Asset Studio.exe — cài plugin từ app (Cài đặt → Công cụ) để ghi đường dẫn.")
		return
	OS.create_process(exe, [])
	for i in 60:
		await get_tree().create_timer(1.0).timeout
		if await client.connect_core():
			reconnect()
			return


func _refresh() -> void:
	var on: bool = client != null and client.connected()
	_offline.visible = not on
	_main.visible = on
	_add_box.visible = add_to_project_path != ""
	_add_label.text = "Thêm vào Dự án: " + add_to_project_path
	_style_btn.disabled = not add_to_project_path.get_extension().to_lower() in ["png", "jpg", "jpeg", "webp"]
	for n in [_recipe_opt.get_parent(), _desc, _title.get_parent(), _form, _auto_import, _run_btn]:
		n.visible = add_to_project_path == ""
	_run_box.visible = run_id != ""
	var st := str(run.get("status", "…"))
	_run_btn.disabled = run_id != "" and not st in ["succeeded", "failed", "cancelled", "interrupted", "…"]
	_run_label.text = "Run %s — %s" % [run_id.left(8), st]
	var done := 0.0
	var lines := PackedStringArray()
	for s in steps:
		var ss := str(s.get("status"))
		if ss in ["succeeded", "cached", "skipped"]:
			done += 1.0
		elif ss == "running" and s.get("progress") != null:
			done += float(s.get("progress"))
		var line := "%s — %s" % [s.get("key"), ss]
		if s.get("error") is Dictionary:
			line += " (%s)" % s["error"].get("message", "")
		lines.append(line)
	_progress.value = done / steps.size() if steps.size() > 0 else 0.0
	_steps_label.text = "\n".join(lines)
	_refresh_gates()


func _refresh_gates() -> void:
	var open := steps.filter(func(s): return str(s.get("status")) == "awaiting_human")
	var sig := ",".join(open.map(func(s): return str(s.get("id")))) + "|" + ",".join(picked.keys()) + "|" + str(thumbs.size())
	if sig == _gate_sig:
		return
	_gate_sig = sig
	for c in _gates.get_children():
		c.queue_free()
	for s in open:
		_gates.add_child(_gate_view(s))


func _gate_view(step: Dictionary) -> Control:
	var box := VBoxContainer.new()
	var approve := str(step.get("kind")) == "gate.approve"
	var k := StudioClient.gate_k(step)
	var title := Label.new()
	title.text = ("Duyệt: %s" if approve else "Chọn %d: %%s" % k) % step.get("key")
	box.add_child(title)
	var flow := HFlowContainer.new()
	box.add_child(flow)
	var cands := StudioClient.candidates(step)
	for b in cands:
		var h := str(b.get("hash"))
		var btn := Button.new()
		btn.custom_minimum_size = Vector2(THUMB, THUMB)
		btn.expand_icon = true
		btn.toggle_mode = not approve
		btn.button_pressed = picked.has(h)
		if thumbs.get(h) is Texture2D:
			btn.icon = thumbs[h]
		else:
			btn.text = h.left(8)
			_load_thumb(b)
		btn.toggled.connect(func(on):
			if on:
				if k == 1:
					for c in cands:
						picked.erase(str(c.get("hash")))
				picked[h] = true
			else:
				picked.erase(h)
			_refresh_gates())
		flow.add_child(btn)
	var row := HBoxContainer.new()
	var sid := str(step.get("id"))
	if approve:
		for pair in [["Duyệt", true], ["Từ chối", false]]:
			var b := Button.new()
			b.text = pair[0]
			var ok: bool = pair[1]
			b.pressed.connect(func(): _act(func(): return await client.request("POST", "/api/gates/" + sid, {"approved": ok})))
			row.add_child(b)
	else:
		var mine := cands.map(func(c): return str(c.get("hash"))).filter(func(x): return picked.has(x))
		var b := Button.new()
		b.text = "Chọn (%d/%d)" % [mine.size(), k]
		b.disabled = mine.size() != k
		b.pressed.connect(func(): _act(func(): return await client.request("POST", "/api/gates/" + sid, {"picked": mine})))
		row.add_child(b)
	box.add_child(row)
	return box


func _load_thumb(blob: Dictionary) -> void:
	var h := str(blob.get("hash"))
	if thumbs.has(h):
		return
	thumbs[h] = null
	var r: Dictionary = await client.request("GET", "/api/blobs/%s/thumb?size=%d" % [h, THUMB * 2], null, true)
	if not r.ok:
		return
	var img := Image.new()
	if img.load_webp_from_buffer(r.data) != OK and img.load_png_from_buffer(r.data) != OK:
		return
	thumbs[h] = ImageTexture.create_from_image(img)
	_gate_sig = ""
	_refresh_gates()
