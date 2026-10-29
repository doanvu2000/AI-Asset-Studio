using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using UnityEditor;
using UnityEngine;

namespace AssetStudio.Bridge
{
    public sealed class AssetStudioWindow : EditorWindow
    {
        const double PollSeconds = 1.0;

        [SerializeField] string recipeId = "";
        [SerializeField] string projectId = "";
        [SerializeField] string runId = "";
        [SerializeField] string runTitle = "";
        [SerializeField] bool autoImport = true;
        [SerializeField] string addToProjectPath = "";
        [SerializeField] List<string> imported = new();

        StudioClient client;
        bool connecting;
        string status = "";
        string error = "";
        List<Dictionary<string, object>> recipes = new();
        List<Dictionary<string, object>> projects = new();
        readonly Dictionary<string, string> values = new();
        Dictionary<string, object> run;
        List<Dictionary<string, object>> steps = new();
        readonly HashSet<string> picked = new();
        readonly Dictionary<string, Texture2D> thumbs = new();
        readonly HashSet<string> thumbLoading = new();
        bool busy;
        bool importedThisRun;
        double nextPoll;
        Vector2 scroll;

        [MenuItem("Window/AI Asset Studio", false, 2100)]
        public static AssetStudioWindow ShowWindow()
        {
            var w = GetWindow<AssetStudioWindow>("AI Asset Studio");
            w.minSize = new Vector2(360, 420);
            return w;
        }

        /// <summary>Opens the window on a recipe with some inputs filled (context-menu entry points).</summary>
        public static void Open(string recipe, Dictionary<string, string> prefill)
        {
            var w = ShowWindow();
            w.recipeId = recipe;
            w.addToProjectPath = "";
            w.values.Clear();
            w.pendingPrefill = prefill;
            w.ApplyRecipeDefaults();
            w.Focus();
        }

        public static void OpenAddToProject(string assetPath)
        {
            var w = ShowWindow();
            w.addToProjectPath = assetPath;
            w.Focus();
        }

        Dictionary<string, string> pendingPrefill;

        void OnEnable()
        {
            EditorApplication.update += Tick;
            _ = ConnectAsync();
        }

        void OnDisable()
        {
            EditorApplication.update -= Tick;
            client?.Dispose();
            client = null;
            foreach (var t in thumbs.Values) if (t != null) DestroyImmediate(t);
            thumbs.Clear();
        }

        async Task ConnectAsync()
        {
            if (connecting) return;
            connecting = true;
            error = "";
            status = "Đang tìm AI Asset Studio…";
            try
            {
                client?.Dispose();
                client = await StudioClient.ConnectAsync();
                if (client == null)
                {
                    status = "";
                    return;
                }
                recipes = await StudioRuns.RecipesAsync(client);
                projects = await StudioRuns.ProjectsAsync(client);
                status = $"Đã kết nối ({recipes.Count} quy trình)";
                ApplyRecipeDefaults();
            }
            catch (Exception e)
            {
                error = e.Message;
            }
            finally
            {
                connecting = false;
                Repaint();
            }
        }

        Dictionary<string, object> Recipe => recipes.FirstOrDefault(r => J.Str(r, "id") == recipeId);

        void ApplyRecipeDefaults()
        {
            var r = Recipe;
            if (r == null) return;
            foreach (var kv in J.Obj(r, "inputs") ?? new Dictionary<string, object>())
                if (!values.ContainsKey(kv.Key)) values[kv.Key] = StudioRuns.DefaultText(kv.Value);
            if (pendingPrefill != null)
            {
                foreach (var kv in pendingPrefill) values[kv.Key] = kv.Value;
                pendingPrefill = null;
            }
        }

        void Tick()
        {
            if (client == null || string.IsNullOrEmpty(runId) || busy || EditorApplication.timeSinceStartup < nextPoll) return;
            if (run != null && StudioRuns.IsTerminal(J.Str(run, "status")) && (importedThisRun || !autoImport)) return;
            nextPoll = EditorApplication.timeSinceStartup + PollSeconds;
            _ = PollAsync();
        }

        async Task PollAsync()
        {
            busy = true;
            try
            {
                (run, steps) = await StudioRuns.GetRunAsync(client, runId);
                foreach (var s in steps.Where(s => J.Str(s, "status") == "awaiting_human"))
                    foreach (var b in StudioRuns.Candidates(s)) _ = LoadThumbAsync(b);
                if (J.Str(run, "status") == "succeeded" && autoImport && !importedThisRun)
                {
                    importedThisRun = true;
                    await ImportAsync();
                }
            }
            catch (Exception e)
            {
                error = e.Message;
                if (e is StudioException { Code: "core_unreachable" }) client = null;
            }
            finally
            {
                busy = false;
                Repaint();
            }
        }

        async Task LoadThumbAsync(Dictionary<string, object> blob)
        {
            var hash = J.Str(blob, "hash");
            var mime = J.Str(blob, "mime");
            if (thumbs.ContainsKey(hash) || !thumbLoading.Add(hash) || (mime != "image/png" && mime != "image/jpeg")) return;
            try
            {
                var bytes = await client.GetBytesAsync($"/api/blobs/{hash}");
                var tex = new Texture2D(2, 2) { hideFlags = HideFlags.HideAndDontSave };
                if (tex.LoadImage(bytes)) thumbs[hash] = tex;
                else DestroyImmediate(tex);
            }
            catch (Exception)
            {
                thumbs[hash] = null;
            }
            finally
            {
                thumbLoading.Remove(hash);
                Repaint();
            }
        }

        async Task ImportAsync()
        {
            status = "Đang nhận output vào Assets…";
            Repaint();
            var dirs = await StudioRuns.ImportOutputsAsync(client, runId);
            imported = dirs;
            AssetDatabase.Refresh();
            status = dirs.Count > 0 ? $"Đã nhận {dirs.Count} thư mục vào {StudioRuns.OutputRoot}" : "Quy trình không có output để nhận";
            var first = dirs.FirstOrDefault();
            if (first != null) EditorGUIUtility.PingObject(AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(first));
        }

        async Task StartAsync()
        {
            busy = true;
            error = "";
            try
            {
                var r = Recipe;
                var specs = J.Obj(r, "inputs") ?? new Dictionary<string, object>();
                var inputs = new Dictionary<string, object>();
                foreach (var kv in specs)
                {
                    values.TryGetValue(kv.Key, out var text);
                    var type = J.Str(kv.Value, "type", "text");
                    if (StudioRuns.BlobTypes.Contains(type))
                    {
                        if (string.IsNullOrWhiteSpace(text)) continue;
                        inputs[kv.Key] = AbsolutePath(text);
                        continue;
                    }
                    if (string.IsNullOrEmpty(text) && !StudioRuns.Required(kv.Value) && type != "text") continue;
                    inputs[kv.Key] = StudioRuns.ParseValue(kv.Value, text);
                }
                status = "Đang gửi…";
                Repaint();
                runId = await StudioRuns.StartAsync(client, r, inputs, projectId, runTitle, "unity");
                run = null;
                steps.Clear();
                picked.Clear();
                imported.Clear();
                importedThisRun = false;
                nextPoll = 0;
                status = "Đã bắt đầu";
            }
            catch (Exception e)
            {
                error = e.Message;
            }
            finally
            {
                busy = false;
                Repaint();
            }
        }

        static string AbsolutePath(string p) => Path.IsPathRooted(p) ? p : Path.GetFullPath(p);

        async Task Act(Func<Task> action)
        {
            busy = true;
            error = "";
            try
            {
                await action();
                nextPoll = 0;
            }
            catch (Exception e)
            {
                error = e.Message;
            }
            finally
            {
                busy = false;
                Repaint();
            }
        }

        const string LibraryImport = "library-import";

        /// <summary>The file becomes a Library version of the project; its output is not imported back into Assets.</summary>
        async Task AddToLibraryAsync()
        {
            if (string.IsNullOrEmpty(projectId)) throw new StudioException(0, "no_project", "Chọn một Dự án trước");
            var recipe = recipes.FirstOrDefault(r => J.Str(r, "id") == LibraryImport)
                         ?? throw new StudioException(0, "unknown_recipe", "Bản AI Asset Studio này chưa có quy trình nhập Thư viện");
            var inputs = new Dictionary<string, object> { ["files"] = AbsolutePath(addToProjectPath) };
            runId = await StudioRuns.StartAsync(client, recipe, inputs, projectId, Path.GetFileName(addToProjectPath), "unity");
            run = null;
            steps.Clear();
            imported.Clear();
            importedThisRun = true;
            status = $"Đang nhập {Path.GetFileName(addToProjectPath)} vào Thư viện";
            addToProjectPath = "";
        }

        async Task SetStyleReferenceAsync()
        {
            if (string.IsNullOrEmpty(projectId)) throw new StudioException(0, "no_project", "Chọn một Dự án trước");
            var hash = await client.UploadAsync(AbsolutePath(addToProjectPath));
            var project = await client.GetAsync($"/api/projects/{Uri.EscapeDataString(projectId)}");
            var style = J.Obj(project, "style") ?? new Dictionary<string, object>();
            style["reference_image"] = hash;
            await client.PatchAsync($"/api/projects/{Uri.EscapeDataString(projectId)}", new Dictionary<string, object> { ["style"] = style });
            status = $"Đã đặt {Path.GetFileName(addToProjectPath)} làm ảnh style của Dự án";
            addToProjectPath = "";
        }

        void OnGUI()
        {
            scroll = EditorGUILayout.BeginScrollView(scroll);
            DrawHeader();
            if (client != null)
            {
                if (!string.IsNullOrEmpty(addToProjectPath)) DrawAddToProject();
                else DrawForm();
                DrawRun();
            }
            EditorGUILayout.EndScrollView();
        }

        void DrawHeader()
        {
            using (new EditorGUILayout.HorizontalScope())
            {
                GUILayout.Label("AI Asset Studio", EditorStyles.boldLabel);
                GUILayout.FlexibleSpace();
                using (new EditorGUI.DisabledScope(connecting))
                    if (GUILayout.Button("Kết nối lại", GUILayout.Width(90))) _ = ConnectAsync();
            }
            if (client == null && !connecting)
            {
                EditorGUILayout.HelpBox("Không thấy AI Asset Studio đang chạy trên máy này. Mở app rồi bấm Kết nối lại.", MessageType.Info);
                if (GUILayout.Button("Mở AI Asset Studio"))
                {
                    if (StudioLocator.OpenApp()) EditorApplication.delayCall += () => _ = WaitForCoreAsync();
                    else error = "Không tìm thấy AI Asset Studio.exe — cài plugin từ app (Cài đặt → Công cụ) để ghi đường dẫn.";
                }
                var dir = EditorGUILayout.DelayedTextField("Thư mục dữ liệu", StudioLocator.DataDirOverride);
                if (dir != StudioLocator.DataDirOverride) StudioLocator.DataDirOverride = dir;
            }
            if (!string.IsNullOrEmpty(status)) EditorGUILayout.LabelField(status, EditorStyles.miniLabel);
            if (!string.IsNullOrEmpty(error)) EditorGUILayout.HelpBox(error, MessageType.Error);
        }

        async Task WaitForCoreAsync()
        {
            for (var i = 0; i < 60 && client == null; i++)
            {
                await Task.Delay(1000);
                await ConnectAsync();
            }
        }

        void DrawAddToProject()
        {
            EditorGUILayout.Space();
            EditorGUILayout.LabelField("Thêm vào Dự án", EditorStyles.boldLabel);
            EditorGUILayout.LabelField(addToProjectPath, EditorStyles.wordWrappedMiniLabel);
            DrawProjectPopup(false);
            EditorGUILayout.HelpBox("Nhập vào Thư viện: file thành asset của Dự án (duyệt, tìm tương tự, đóng gói). " +
                                    "Ảnh style: ảnh này thành tham chiếu style, các quy trình chạy trong Dự án bám theo nó.", MessageType.None);
            var image = new[] { ".png", ".jpg", ".jpeg", ".webp" }.Contains(Path.GetExtension(addToProjectPath).ToLowerInvariant());
            using (new EditorGUILayout.HorizontalScope())
            {
                using (new EditorGUI.DisabledScope(busy || string.IsNullOrEmpty(projectId)))
                {
                    if (GUILayout.Button("Nhập vào Thư viện")) _ = Act(AddToLibraryAsync);
                    using (new EditorGUI.DisabledScope(!image))
                        if (GUILayout.Button("Đặt làm ảnh style")) _ = Act(SetStyleReferenceAsync);
                }
                if (GUILayout.Button("Huỷ")) addToProjectPath = "";
            }
        }

        void DrawProjectPopup(bool allowNone)
        {
            var names = new List<string>();
            var ids = new List<string>();
            if (allowNone) { names.Add("(không có)"); ids.Add(""); }
            foreach (var p in projects) { names.Add(J.Str(p, "name")); ids.Add(J.Str(p, "id")); }
            var idx = Mathf.Max(0, ids.IndexOf(projectId));
            if (ids.Count == 0) { EditorGUILayout.LabelField("Dự án", "chưa có Dự án nào"); return; }
            projectId = ids[EditorGUILayout.Popup("Dự án", idx, names.ToArray())];
        }

        void DrawForm()
        {
            EditorGUILayout.Space();
            var names = recipes.Select(r => $"{J.Str(r, "name")}  ·  {J.Str(r, "id")}").ToArray();
            var idx = recipes.FindIndex(r => J.Str(r, "id") == recipeId);
            var next = EditorGUILayout.Popup("Quy trình", Mathf.Max(0, idx), names);
            if (recipes.Count > 0 && (idx < 0 || next != idx))
            {
                recipeId = J.Str(recipes[next], "id");
                values.Clear();
                ApplyRecipeDefaults();
            }
            var r = Recipe;
            if (r == null) return;
            var desc = J.Str(r, "description");
            if (desc.Length > 0) EditorGUILayout.LabelField(desc, EditorStyles.wordWrappedMiniLabel);
            DrawProjectPopup(true);
            runTitle = EditorGUILayout.TextField("Tiêu đề", runTitle);
            foreach (var kv in J.Obj(r, "inputs") ?? new Dictionary<string, object>()) DrawInput(kv.Key, kv.Value);
            autoImport = EditorGUILayout.Toggle("Tự nhận output vào Assets", autoImport);
            var running = run != null && !StudioRuns.IsTerminal(J.Str(run, "status"));
            using (new EditorGUI.DisabledScope(busy || running))
                if (GUILayout.Button("Chạy", GUILayout.Height(26))) _ = StartAsync();
        }

        void DrawInput(string name, object spec)
        {
            var label = J.Str(spec, "label", name);
            if (label.Length > 48) label = label.Substring(0, 46) + "…";
            var content = new GUIContent(label + (StudioRuns.Required(spec) ? " *" : ""), J.Str(spec, "label", name));
            values.TryGetValue(name, out var v);
            v ??= "";
            var type = J.Str(spec, "type", "text");
            var options = J.Arr(spec, "options");
            if (options != null && options.Count > 0)
            {
                var opts = options.Select(o => o is double d ? d.ToString("R", System.Globalization.CultureInfo.InvariantCulture) : Convert.ToString(o, System.Globalization.CultureInfo.InvariantCulture)).ToList();
                var i = Mathf.Max(0, opts.IndexOf(v));
                values[name] = opts[EditorGUILayout.Popup(content, i, opts.Select(o => new GUIContent(o)).ToArray())];
                return;
            }
            switch (type)
            {
                case "bool":
                    values[name] = EditorGUILayout.Toggle(content, v == "true") ? "true" : "false";
                    return;
                case "image":
                case "mesh":
                case "file":
                case "video":
                    using (new EditorGUILayout.HorizontalScope())
                    {
                        values[name] = EditorGUILayout.TextField(content, v);
                        if (GUILayout.Button("…", GUILayout.Width(24)))
                        {
                            var exts = J.Arr(spec, "ext");
                            var chosen = EditorUtility.OpenFilePanel(label, Path.GetDirectoryName(AbsolutePath(v.Length > 0 ? v : "Assets")) ?? "",
                                exts != null && exts.Count > 0 ? string.Join(",", exts) : "");
                            if (!string.IsNullOrEmpty(chosen)) values[name] = chosen;
                        }
                    }
                    var dropped = DropTarget();
                    if (dropped != null) values[name] = dropped;
                    return;
            }
            if (J.Str(spec, "path") == "dir")
            {
                using (new EditorGUILayout.HorizontalScope())
                {
                    values[name] = EditorGUILayout.TextField(content, v);
                    if (GUILayout.Button("…", GUILayout.Width(24)))
                    {
                        var dir = EditorUtility.OpenFolderPanel(label, AbsolutePath(v.Length > 0 ? v : "Assets"), "");
                        if (!string.IsNullOrEmpty(dir)) values[name] = dir;
                    }
                }
                return;
            }
            values[name] = J.Bool(spec, "multiline")
                ? EditorGUILayout.TextField(content, v, EditorStyles.textArea, GUILayout.MinHeight(38))
                : EditorGUILayout.TextField(content, v);
        }

        /// <summary>Lets an asset be dragged from the Project window onto the file field above it.</summary>
        static string DropTarget()
        {
            var rect = GUILayoutUtility.GetLastRect();
            var e = Event.current;
            if (!rect.Contains(e.mousePosition) || (e.type != EventType.DragUpdated && e.type != EventType.DragPerform)) return null;
            DragAndDrop.visualMode = DragAndDropVisualMode.Copy;
            if (e.type != EventType.DragPerform) return null;
            DragAndDrop.AcceptDrag();
            var path = DragAndDrop.paths.FirstOrDefault();
            return path == null ? null : AbsolutePath(path);
        }

        void DrawRun()
        {
            if (string.IsNullOrEmpty(runId)) return;
            EditorGUILayout.Space();
            var st = run == null ? "…" : J.Str(run, "status");
            EditorGUILayout.LabelField($"Run {runId.Substring(0, Math.Min(8, runId.Length))}", st, EditorStyles.boldLabel);
            if (steps.Count > 0)
            {
                var done = steps.Count(s => J.Str(s, "status") is "succeeded" or "cached" or "skipped");
                var partial = steps.Where(s => J.Str(s, "status") == "running").Sum(s => J.Num(s, "progress"));
                var rect = GUILayoutUtility.GetRect(18, 18, GUILayout.ExpandWidth(true));
                EditorGUI.ProgressBar(rect, (float)((done + partial) / steps.Count), $"{done}/{steps.Count} bước");
                foreach (var s in steps)
                {
                    var line = $"{J.Str(s, "key")} — {J.Str(s, "status")}";
                    var err = J.Obj(s, "error");
                    if (err != null) line += $"  ({J.Str(err, "message")})";
                    EditorGUILayout.LabelField(line, EditorStyles.miniLabel);
                }
            }
            foreach (var s in steps.Where(s => J.Str(s, "status") == "awaiting_human")) DrawGate(s);
            using (new EditorGUILayout.HorizontalScope())
            {
                var terminal = run != null && StudioRuns.IsTerminal(st);
                using (new EditorGUI.DisabledScope(busy || terminal))
                    if (GUILayout.Button("Huỷ run")) _ = Act(() => client.PostAsync($"/api/runs/{runId}/cancel"));
                using (new EditorGUI.DisabledScope(busy || st != "succeeded"))
                    if (GUILayout.Button("Nhận output")) _ = Act(ImportAsync);
            }
            foreach (var dir in imported)
                if (GUILayout.Button(dir, EditorStyles.linkLabel))
                    EditorGUIUtility.PingObject(AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(dir));
        }

        void DrawGate(Dictionary<string, object> step)
        {
            var stepId = J.Str(step, "id");
            var approve = StudioRuns.IsApproveGate(step);
            var k = Math.Max(1, StudioRuns.GateK(step));
            EditorGUILayout.Space();
            EditorGUILayout.LabelField(approve ? $"Duyệt: {J.Str(step, "key")}" : $"Chọn {k}: {J.Str(step, "key")}", EditorStyles.boldLabel);
            var cands = StudioRuns.Candidates(step);
            const float size = 96;
            var perRow = Mathf.Max(1, (int)((position.width - 24) / (size + 6)));
            for (var i = 0; i < cands.Count; i += perRow)
            {
                using (new EditorGUILayout.HorizontalScope())
                {
                    foreach (var b in cands.Skip(i).Take(perRow))
                    {
                        var hash = J.Str(b, "hash");
                        thumbs.TryGetValue(hash, out var tex);
                        var on = picked.Contains(hash);
                        var style = new GUIStyle(GUI.skin.button) { padding = new RectOffset(2, 2, 2, 2) };
                        var content = tex != null ? new GUIContent(tex) : new GUIContent(J.Str(b, "name", hash.Substring(0, 8)));
                        var bg = GUI.backgroundColor;
                        if (on) GUI.backgroundColor = new Color(0.35f, 0.75f, 1f);
                        if (GUILayout.Button(content, style, GUILayout.Width(size), GUILayout.Height(size)) && !approve)
                        {
                            if (on) picked.Remove(hash);
                            else
                            {
                                if (k == 1) picked.RemoveWhere(h => cands.Any(c => J.Str(c, "hash") == h));
                                picked.Add(hash);
                            }
                        }
                        GUI.backgroundColor = bg;
                    }
                }
            }
            using (new EditorGUILayout.HorizontalScope())
            using (new EditorGUI.DisabledScope(busy))
            {
                if (approve)
                {
                    if (GUILayout.Button("Duyệt")) _ = Act(() => StudioRuns.ResolveGateAsync(client, stepId, null, true));
                    if (GUILayout.Button("Từ chối")) _ = Act(() => StudioRuns.ResolveGateAsync(client, stepId, null, false));
                }
                else
                {
                    var mine = cands.Select(c => J.Str(c, "hash")).Where(picked.Contains).ToList();
                    using (new EditorGUI.DisabledScope(mine.Count != k))
                        if (GUILayout.Button($"Chọn ({mine.Count}/{k})")) _ = Act(() => StudioRuns.ResolveGateAsync(client, stepId, mine, null));
                }
            }
        }
    }
}
