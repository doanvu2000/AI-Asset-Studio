@tool
extends Node
## HTTP client for the local AI Asset Studio core, found through `core.lock.json`
## (same contract as the MCP CoreLink). The token stays in memory; it is never printed.

const LOCK_NAME := "core.lock.json"
const APP_NAME := "AI Asset Studio"
const CONFIG_PATH := "res://addons/asset_studio/bridge_config.json"
const SETTING_DATA_DIR := "asset_studio/data_dir"
const BLOB_TYPES := ["image", "mesh", "file", "video"]

var base_url := ""
var data_dir := ""
var _token := ""


func connected() -> bool:
	return base_url != ""


static func install_config() -> Dictionary:
	if not FileAccess.file_exists(CONFIG_PATH):
		return {}
	var v = JSON.parse_string(FileAccess.get_file_as_string(CONFIG_PATH))
	return v if v is Dictionary else {}


static func candidate_data_dirs() -> PackedStringArray:
	var dirs := PackedStringArray()
	var es: EditorSettings = EditorInterface.get_editor_settings() if Engine.is_editor_hint() else null
	var add := func(d: String) -> void:
		d = d.strip_edges().replace("\\", "/")
		if d != "" and not dirs.has(d):
			dirs.append(d)
	if es and es.has_setting(SETTING_DATA_DIR):
		add.call(str(es.get_setting(SETTING_DATA_DIR)))
	add.call(str(install_config().get("data_dir", "")))
	add.call(OS.get_environment("STUDIO_DATA_DIR"))
	var appdata := OS.get_environment("APPDATA")
	if appdata != "":
		add.call(appdata.path_join(APP_NAME).path_join("data"))
	return dirs


static func app_exe() -> String:
	var exe := str(install_config().get("app_exe", ""))
	# The install config sits in the project, so a cloned project must not be able to point "Open app" at its own exe.
	var project := ProjectSettings.globalize_path("res://").simplify_path().to_lower()
	var slashed := exe.replace("\\", "/")
	var norm := slashed.simplify_path().to_lower()
	var trusted := exe != "" and not slashed.begins_with("//") and FileAccess.file_exists(exe)
	trusted = trusted and norm.get_file() == (APP_NAME + ".exe").to_lower() and not norm.begins_with(project)
	if trusted:
		return exe
	for root in [OS.get_environment("LOCALAPPDATA").path_join("Programs"), OS.get_environment("ProgramFiles")]:
		var p: String = str(root).path_join(APP_NAME).path_join(APP_NAME + ".exe")
		if FileAccess.file_exists(p):
			return p
	return ""


func connect_core() -> bool:
	base_url = ""
	_token = ""
	for dir in candidate_data_dirs():
		var path := dir.path_join(LOCK_NAME)
		if not FileAccess.file_exists(path):
			continue
		var info = JSON.parse_string(FileAccess.get_file_as_string(path))
		if not (info is Dictionary) or not info.has("port") or str(info.get("token", "")) == "":
			continue
		# OS.is_process_running only knows processes Godot started; the token probe below rules out a stale lock.
		if int(info.get("pid", 0)) <= 0:
			continue
		# The lock path can come from a project-controlled config; never send its token off this machine.
		var host := "localhost" if str(info.get("host", "")) == "localhost" else "127.0.0.1"
		base_url = "http://%s:%d" % [host, int(info["port"])]
		_token = str(info["token"])
		var r := await request("GET", "/api/runs?limit=1", null, false, PackedByteArray(), "", 3.0)
		if r.ok:
			data_dir = dir
			return true
	base_url = ""
	_token = ""
	return false


## {ok, status, data, error}; `data` is the parsed JSON body (or raw bytes when `raw`).
func request(method: String, path: String, body = null, raw := false, upload: PackedByteArray = PackedByteArray(), upload_name := "",
		timeout_s := 900.0) -> Dictionary:
	if base_url == "":
		return {"ok": false, "status": 0, "data": null, "error": "core_unreachable: not connected"}
	var http := HTTPRequest.new()
	http.timeout = timeout_s
	add_child(http)
	var headers := PackedStringArray(["Authorization: Bearer " + _token])
	var m: int = {"GET": HTTPClient.METHOD_GET, "POST": HTTPClient.METHOD_POST, "PATCH": HTTPClient.METHOD_PATCH}[method]
	var err: int
	if upload_name != "":
		var boundary := "asb" + str(Time.get_ticks_usec())
		headers.append("Content-Type: multipart/form-data; boundary=" + boundary)
		var payload := ("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\nContent-Type: application/octet-stream\r\n\r\n"
			% [boundary, upload_name.replace("\"", "_")]).to_utf8_buffer()
		payload.append_array(upload)
		payload.append_array(("\r\n--%s--\r\n" % boundary).to_utf8_buffer())
		err = http.request_raw(base_url + path, headers, m, payload)
	elif body != null or method != "GET":
		headers.append("Content-Type: application/json")
		err = http.request(base_url + path, headers, m, JSON.stringify(body if body != null else {}))
	else:
		err = http.request(base_url + path, headers, m)
	if err != OK:
		http.queue_free()
		return {"ok": false, "status": 0, "data": null, "error": "core_unreachable: request failed (%d)" % err}
	var res: Array = await http.request_completed
	http.queue_free()
	var result: int = res[0]
	var code: int = res[1]
	var bytes: PackedByteArray = res[3]
	if result != HTTPRequest.RESULT_SUCCESS:
		return {"ok": false, "status": 0, "data": null, "error": "core_unreachable: result %d" % result}
	if code >= 400:
		var parsed = JSON.parse_string(bytes.get_string_from_utf8())
		var e: Dictionary = parsed.get("error", {}) if parsed is Dictionary else {}
		return {"ok": false, "status": code, "data": null,
			"error": "%s: %s" % [e.get("code", "http_error"), e.get("message", bytes.get_string_from_utf8().left(300))]}
	if raw:
		return {"ok": true, "status": code, "data": bytes, "error": ""}
	var text := bytes.get_string_from_utf8()
	return {"ok": true, "status": code, "data": JSON.parse_string(text) if text != "" else null, "error": ""}


func upload(abs_path: String) -> Dictionary:
	var bytes := FileAccess.get_file_as_bytes(abs_path)
	if bytes.is_empty() and FileAccess.get_open_error() != OK:
		return {"ok": false, "error": "cannot read " + abs_path}
	var r := await request("POST", "/api/blobs", null, false, bytes, abs_path.get_file())
	if r.ok:
		r["hash"] = str(r.data.get("hash", ""))
	return r


## An atlas brings the page images it names; an Aseprite JSON sheet brings its PNG (as the MCP server does).
static func with_siblings(spec: Dictionary, path: String) -> PackedStringArray:
	var files := PackedStringArray([path])
	var dir := path.get_base_dir()
	match str(spec.get("siblings", "")):
		"atlas_pages":
			for line in FileAccess.get_file_as_string(path).split("\n"):
				var page := line.strip_edges()
				if page != "" and not ":" in page and page.to_lower().ends_with(".png") and FileAccess.file_exists(dir.path_join(page)):
					files.append(dir.path_join(page))
		"aseprite_sheet":
			if path.to_lower().ends_with(".json"):
				var doc = JSON.parse_string(FileAccess.get_file_as_string(path))
				var image := ""
				if doc is Dictionary and doc.get("meta") is Dictionary:
					image = str(doc["meta"].get("image", "")).get_file()
				files.append(dir.path_join(image) if image != "" else path.get_basename() + ".png")
	return files


## Uploads file inputs (absolute paths) and starts the run; returns {ok, run_id, error}.
func start_run(recipe: Dictionary, inputs: Dictionary, project_id: String, title: String) -> Dictionary:
	var specs: Dictionary = recipe.get("inputs", {})
	var values := {}
	for key in inputs:
		if not specs.has(key):
			continue
		var spec: Dictionary = specs[key]
		var v = inputs[key]
		if str(spec.get("type", "")) in BLOB_TYPES:
			if str(v).strip_edges() == "":
				continue
			var hashes := []
			for f in with_siblings(spec, ProjectSettings.globalize_path(str(v))):
				var up := await upload(f)
				if not up.ok:
					return {"ok": false, "error": "upload %s: %s" % [f.get_file(), up.error]}
				hashes.append(up.hash)
			v = hashes[0] if hashes.size() == 1 else hashes
		values[key] = v
	var body := {"recipe_id": recipe.get("id", ""), "inputs": values, "title": title,
		"params": {"$meta": {"source": "godot"}}}
	if project_id != "":
		body["project_id"] = project_id
	var r := await request("POST", "/api/runs", body)
	if r.ok:
		r["run_id"] = str(r.data.get("run_id", ""))
	return r


## Gate candidates in the engine's PickGate order, so "first" matches what the app shows first.
static func candidates(step: Dictionary) -> Array:
	var seen := {}
	var out := []
	var inputs = step.get("inputs", {})
	if not (inputs is Dictionary):
		return out
	for refs in inputs.values():
		for b in refs:
			if b is Dictionary and not seen.has(b.get("hash")):
				seen[b.get("hash")] = true
				out.append(b)
	return out


static func gate_k(step: Dictionary) -> int:
	var meta = step.get("meta", {})
	var gate = meta.get("gate", {}) if meta is Dictionary else {}
	if gate is Dictionary and gate.has("k"):
		return maxi(1, int(gate["k"]))
	var params = step.get("params", {})
	return maxi(1, int(params.get("k", 1))) if params is Dictionary else 1


static func safe(s: String, fallback := "asset") -> String:
	var re := RegEx.create_from_string("[^\\w\\-.]+")
	var v := re.sub(s, "_", true).strip_edges().trim_prefix("_").trim_suffix("_")
	return fallback if v == "" else v.left(60)


## Animated GIF/WebP previews: engines cannot import them (Godot fails on animated WebP, Unity skips WebP).
const PREVIEW_ONLY := ["image/gif", "image/webp"]
const EXTS := {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif",
	"model/gltf-binary": ".glb", "model/gltf+json": ".gltf", "application/json": ".json",
	"video/mp4": ".mp4", "video/webm": ".webm"}


## Copies a finished run's published outputs into res://asset_studio/<recipe>/: the core's Godot exporter folder
## when the step supports one, the raw files otherwise. Returns {ok, dirs, error}.
func import_outputs(run_id: String, root := "res://asset_studio") -> Dictionary:
	var r := await request("GET", "/api/runs/" + run_id.uri_encode())
	if not r.ok:
		return r
	var run: Dictionary = r.data.get("run", {})
	var base := root.path_join(safe(str(run.get("recipe_id", "")), "recipe"))
	var dirs := []
	for s in r.data.get("steps", []):
		var meta = s.get("meta", {})
		var spec = meta.get("spec", {}) if meta is Dictionary else {}
		var publish = spec.get("publish", {}) if spec is Dictionary else {}
		if not (publish is Dictionary) or publish.is_empty() or not str(s.get("status")) in ["succeeded", "cached"]:
			continue
		var step_id := str(s.get("id"))
		var opts := await request("GET", "/api/steps/%s/export" % step_id)
		if not opts.ok:
			return opts
		if "godot" in opts.data.get("engines", []):
			var ex := await request("POST", "/api/steps/%s/export" % step_id, {"engine": "godot"})
			if not ex.ok:
				return ex
			var dest := base.path_join("%s-%s" % [safe(str(ex.data.get("name", ""))), step_id.left(8)])
			var err := _copy_dir(str(ex.data.get("dir", "")), ProjectSettings.globalize_path(dest))
			if err != "":
				return {"ok": false, "error": err}
			dirs.append(dest)
			continue
		var raw_dir := base.path_join("%s-%s" % [safe(str(s.get("key", ""))), step_id.left(8)])
		var outputs: Dictionary = s.get("outputs", {}) if s.get("outputs") is Dictionary else {}
		for out_name in publish:
			var blobs: Array = outputs.get(str(publish[out_name]), [])
			for i in blobs.size():
				var b: Dictionary = blobs[i]
				if str(b.get("mime", "")) in PREVIEW_ONLY:
					continue
				var stem := str(out_name) if blobs.size() == 1 else "%s_%02d" % [out_name, i + 1]
				var ext: String = EXTS.get(str(b.get("mime", "")), "." + str(b.get("name", "x.bin")).get_extension())
				var got := await request("GET", "/api/blobs/" + str(b.get("hash")), null, true)
				if not got.ok:
					return got
				DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path(raw_dir))
				var f := FileAccess.open(raw_dir.path_join(safe(stem) + ext), FileAccess.WRITE)
				f.store_buffer(got.data)
				f.close()
		if DirAccess.dir_exists_absolute(ProjectSettings.globalize_path(raw_dir)):
			dirs.append(raw_dir)
	return {"ok": true, "dirs": dirs, "error": ""}


## Replaces an earlier import of the same step so stale files do not linger.
static func _copy_dir(src: String, dest: String) -> String:
	if src == "" or not DirAccess.dir_exists_absolute(src):
		return "export folder not found: " + src
	if DirAccess.dir_exists_absolute(dest):
		_remove_dir(dest)
	return _copy_tree(src, dest)


static func _copy_tree(src: String, dest: String) -> String:
	DirAccess.make_dir_recursive_absolute(dest)
	var d := DirAccess.open(src)
	if d == null:
		return "cannot open " + src
	for f in d.get_files():
		var err := DirAccess.copy_absolute(src.path_join(f), dest.path_join(f))
		if err != OK:
			return "copy %s failed (%d)" % [f, err]
	for sub in d.get_directories():
		var e := _copy_tree(src.path_join(sub), dest.path_join(sub))
		if e != "":
			return e
	return ""


static func _remove_dir(path: String) -> void:
	var d := DirAccess.open(path)
	if d == null:
		return
	for f in d.get_files():
		DirAccess.remove_absolute(path.path_join(f))
	for sub in d.get_directories():
		_remove_dir(path.path_join(sub))
	DirAccess.remove_absolute(path)
