using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading.Tasks;

namespace AssetStudio.Bridge
{
    /// <summary>Recipe runs over the core HTTP API (the same calls the MCP server makes) and output import.</summary>
    public static class StudioRuns
    {
        public const string OutputRoot = "Assets/AssetStudio";
        public static readonly HashSet<string> BlobTypes = new() { "image", "mesh", "file", "video" };
        static readonly HashSet<string> Terminal = new() { "succeeded", "failed", "cancelled", "interrupted" };
        static readonly Regex SafeName = new(@"[^\w\-.]+");

        public static bool IsTerminal(string status) => Terminal.Contains(status);

        public static string Safe(string s, string fallback = "asset")
        {
            var v = SafeName.Replace(s ?? "", "_").Trim('_', '.');
            return v.Length == 0 ? fallback : (v.Length > 60 ? v.Substring(0, 60) : v);
        }

        public static async Task<List<Dictionary<string, object>>> RecipesAsync(StudioClient c) =>
            ((await c.GetAsync("/api/recipes")) as List<object> ?? new List<object>()).OfType<Dictionary<string, object>>().ToList();

        public static async Task<List<Dictionary<string, object>>> ProjectsAsync(StudioClient c) =>
            ((await c.GetAsync("/api/projects")) as List<object> ?? new List<object>()).OfType<Dictionary<string, object>>().ToList();

        /// <summary>Uploads file inputs (absolute paths) and starts the run; `source` tags it in the app's run list.</summary>
        public static async Task<string> StartAsync(StudioClient c, Dictionary<string, object> recipe, Dictionary<string, object> inputs,
            string projectId, string title, string source)
        {
            var specs = J.Obj(recipe, "inputs") ?? new Dictionary<string, object>();
            var values = new Dictionary<string, object>();
            foreach (var kv in inputs)
            {
                if (!specs.TryGetValue(kv.Key, out var spec)) continue;
                var v = kv.Value;
                if (BlobTypes.Contains(J.Str(spec, "type")) && v is string path)
                {
                    if (string.IsNullOrWhiteSpace(path)) continue;
                    var files = WithSiblings(spec, Path.GetFullPath(path));
                    var hashes = new List<object>();
                    foreach (var f in files) hashes.Add(await c.UploadAsync(f));
                    v = hashes.Count == 1 ? hashes[0] : hashes;
                }
                values[kv.Key] = v;
            }
            var body = new Dictionary<string, object>
            {
                ["recipe_id"] = J.Str(recipe, "id"),
                ["inputs"] = values,
                ["title"] = title ?? "",
                ["params"] = new Dictionary<string, object> { ["$meta"] = new Dictionary<string, object> { ["source"] = source } },
            };
            if (!string.IsNullOrEmpty(projectId)) body["project_id"] = projectId;
            return J.Str(await c.PostAsync("/api/runs", body), "run_id");
        }

        /// <summary>An atlas brings the page images it names; an Aseprite JSON sheet brings its PNG (as the MCP server does).</summary>
        public static List<string> WithSiblings(object spec, string path)
        {
            var files = new List<string> { path };
            var dir = Path.GetDirectoryName(path) ?? "";
            switch (J.Str(spec, "siblings"))
            {
                case "atlas_pages":
                    foreach (var line in File.ReadAllLines(path))
                    {
                        var name = line.Trim();
                        if (name.Length > 0 && !name.Contains(':') && name.EndsWith(".png", StringComparison.OrdinalIgnoreCase)
                            && File.Exists(Path.Combine(dir, name)))
                            files.Add(Path.Combine(dir, name));
                    }
                    break;
                case "aseprite_sheet" when path.EndsWith(".json", StringComparison.OrdinalIgnoreCase):
                    var image = Path.GetFileName(J.Str(J.Obj(MiniJson.Parse(File.ReadAllText(path)), "meta"), "image"));
                    var png = string.IsNullOrEmpty(image) ? Path.ChangeExtension(path, ".png") : Path.Combine(dir, image);
                    if (!File.Exists(png)) throw new StudioException(0, "missing_sheet", $"{Path.GetFileName(png)} is not next to {Path.GetFileName(path)}");
                    files.Add(png);
                    break;
            }
            return files;
        }

        public static async Task<(Dictionary<string, object> run, List<Dictionary<string, object>> steps)> GetRunAsync(StudioClient c, string runId)
        {
            var data = await c.GetAsync($"/api/runs/{Uri.EscapeDataString(runId)}");
            return (J.Obj(data, "run"), (J.Arr(data, "steps") ?? new List<object>()).OfType<Dictionary<string, object>>().ToList());
        }

        /// <summary>Gate candidates in the engine's PickGate order, so "first" matches what the app shows first.</summary>
        public static List<Dictionary<string, object>> Candidates(Dictionary<string, object> step)
        {
            var seen = new HashSet<string>();
            var out_ = new List<Dictionary<string, object>>();
            foreach (var refs in (J.Obj(step, "inputs") ?? new Dictionary<string, object>()).Values)
                foreach (var b in (refs as List<object> ?? new List<object>()).OfType<Dictionary<string, object>>())
                    if (seen.Add(J.Str(b, "hash"))) out_.Add(b);
            return out_;
        }

        public static int GateK(Dictionary<string, object> step)
        {
            var gate = J.Obj(J.Obj(step, "meta"), "gate");
            if (J.Has(gate, "k")) return (int)J.Num(gate, "k", 1);
            return (int)J.Num(J.Obj(step, "params"), "k", 1);
        }

        public static bool IsApproveGate(Dictionary<string, object> step) => J.Str(step, "kind") == "gate.approve";

        public static Task ResolveGateAsync(StudioClient c, string stepId, IEnumerable<string> picked, bool? approved)
        {
            var body = approved.HasValue
                ? new Dictionary<string, object> { ["approved"] = approved.Value }
                : new Dictionary<string, object> { ["picked"] = picked.Cast<object>().ToList() };
            return c.PostAsync($"/api/gates/{Uri.EscapeDataString(stepId)}", body);
        }

        /// <summary>Picks the first k candidates / approves every open gate of the run (MCP gate_policy auto_first).</summary>
        public static async Task<bool> AutoResolveAsync(StudioClient c, List<Dictionary<string, object>> steps)
        {
            var acted = false;
            foreach (var s in steps.Where(s => J.Str(s, "status") == "awaiting_human"))
            {
                if (IsApproveGate(s)) await ResolveGateAsync(c, J.Str(s, "id"), null, true);
                else await ResolveGateAsync(c, J.Str(s, "id"), Candidates(s).Take(Math.Max(1, GateK(s))).Select(b => J.Str(b, "hash")), null);
                acted = true;
            }
            return acted;
        }

        /// <summary>
        /// Copies a finished run's published outputs into Assets/AssetStudio/&lt;recipe&gt;/: the core's Unity exporter
        /// bundle when the step supports one, the raw files otherwise. Returns the project-relative folders written.
        /// </summary>
        public static async Task<List<string>> ImportOutputsAsync(StudioClient c, string runId, string engine = "unity",
            string outputRoot = OutputRoot)
        {
            var (run, steps) = await GetRunAsync(c, runId);
            var root = Path.Combine(outputRoot, Safe(J.Str(run, "recipe_id"), "recipe"));
            var written = new List<string>();
            foreach (var s in steps)
            {
                var publish = J.Obj(J.Obj(J.Obj(s, "meta"), "spec"), "publish");
                var status = J.Str(s, "status");
                if (publish == null || publish.Count == 0 || (status != "succeeded" && status != "cached")) continue;
                var stepId = J.Str(s, "id");
                var opts = await c.GetAsync($"/api/steps/{stepId}/export");
                var engines = (J.Arr(opts, "engines") ?? new List<object>()).Select(e => e?.ToString());
                if (engines.Contains(engine))
                {
                    var res = await c.PostAsync($"/api/steps/{stepId}/export", new Dictionary<string, object> { ["engine"] = engine });
                    var dest = Path.Combine(root, $"{Safe(J.Str(res, "name"))}-{stepId.Substring(0, Math.Min(8, stepId.Length))}");
                    CopyDir(J.Str(res, "dir"), dest);
                    written.Add(dest.Replace('\\', '/'));
                    continue;
                }
                var raw = Path.Combine(root, $"{Safe(J.Str(s, "key"))}-{stepId.Substring(0, Math.Min(8, stepId.Length))}");
                var outputs = J.Obj(s, "outputs") ?? new Dictionary<string, object>();
                foreach (var kv in publish)
                {
                    var blobs = (J.Get(outputs, kv.Value?.ToString() ?? "") as List<object> ?? new List<object>()).OfType<Dictionary<string, object>>().ToList();
                    for (var i = 0; i < blobs.Count; i++)
                    {
                        var b = blobs[i];
                        if (PreviewOnly.Contains(J.Str(b, "mime"))) continue;
                        var stem = blobs.Count == 1 ? kv.Key : $"{kv.Key}_{i + 1:00}";
                        Directory.CreateDirectory(raw);
                        var bytes = await c.GetBytesAsync($"/api/blobs/{J.Str(b, "hash")}");
                        File.WriteAllBytes(Path.Combine(raw, Safe(stem) + Ext(b)), bytes);
                    }
                }
                if (Directory.Exists(raw)) written.Add(raw.Replace('\\', '/'));
            }
            return written;
        }

        static readonly Dictionary<string, string> Exts = new()
        {
            ["image/png"] = ".png", ["image/jpeg"] = ".jpg", ["image/webp"] = ".webp", ["image/gif"] = ".gif",
            ["model/gltf-binary"] = ".glb", ["model/gltf+json"] = ".gltf", ["application/json"] = ".json",
            ["video/mp4"] = ".mp4", ["video/webm"] = ".webm",
        };

        // Animated GIF/WebP previews: engines cannot import them (Godot fails on animated WebP, Unity skips WebP).
        static readonly HashSet<string> PreviewOnly = new() { "image/gif", "image/webp" };

        static string Ext(Dictionary<string, object> blob)
        {
            if (Exts.TryGetValue(J.Str(blob, "mime"), out var e)) return e;
            var fromName = Path.GetExtension(J.Str(blob, "name"));
            return string.IsNullOrEmpty(fromName) ? ".bin" : fromName;
        }

        /// <summary>Replaces an earlier import of the same step: the exporter's GUIDs are stable per step.</summary>
        static void CopyDir(string src, string dest)
        {
            if (string.IsNullOrEmpty(src) || !Directory.Exists(src)) throw new StudioException(0, "export_missing", $"export folder not found: {src}");
            if (Directory.Exists(dest)) Directory.Delete(dest, true);
            var shared = SharedScripts(src, dest);
            foreach (var file in Directory.GetFiles(src, "*", SearchOption.AllDirectories))
            {
                if (shared.TryGetValue(Path.GetFullPath(file), out var have))
                {
                    if (file.EndsWith(".cs", StringComparison.OrdinalIgnoreCase)) File.Copy(file, have, true);
                    continue;
                }
                var rel = file.Substring(src.Length).TrimStart('\\', '/');
                var to = Path.Combine(dest, rel);
                Directory.CreateDirectory(Path.GetDirectoryName(to)!);
                File.Copy(file, to, true);
            }
        }

        static readonly Regex MetaGuid = new(@"^guid: ([0-9a-f]{32})\s*$", RegexOptions.Multiline);

        static string GuidOf(string meta)
        {
            try { var m = MetaGuid.Match(File.ReadAllText(meta)); return m.Success ? m.Groups[1].Value : null; }
            catch (IOException) { return null; }
        }

        /// <summary>
        /// Shared fixed-GUID scripts (NpcData.cs, AssetStudioParallax.cs...) that another import already put in Assets/
        /// map to that copy (export file -> existing file, for the .cs and its .meta): a second copy would be CS0101
        /// plus a GUID clash, so the existing one is refreshed in place instead.
        /// </summary>
        static Dictionary<string, string> SharedScripts(string src, string dest)
        {
            var map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            var wanted = new Dictionary<string, string>();
            foreach (var meta in Directory.GetFiles(src, "*.cs.meta", SearchOption.AllDirectories))
                if (GuidOf(meta) is { } g) wanted[g] = Path.GetFullPath(meta);
            if (wanted.Count == 0 || !Directory.Exists("Assets")) return map;
            var destFull = Path.GetFullPath(dest).TrimEnd('\\', '/') + Path.DirectorySeparatorChar;
            foreach (var meta in Directory.GetFiles("Assets", "*.cs.meta", SearchOption.AllDirectories))
            {
                var full = Path.GetFullPath(meta);
                if (full.StartsWith(destFull, StringComparison.OrdinalIgnoreCase)) continue;
                if (GuidOf(meta) is not { } g || !wanted.TryGetValue(g, out var mine) || map.ContainsKey(mine)) continue;
                map[mine] = full;
                map[mine.Substring(0, mine.Length - 5)] = full.Substring(0, full.Length - 5);
            }
            return map;
        }

        public static object ParseValue(object spec, string text)
        {
            var type = J.Str(spec, "type", "text");
            var inv = CultureInfo.InvariantCulture;
            switch (type)
            {
                case "int": return long.TryParse(text, NumberStyles.Integer, inv, out var l) ? l : 0L;
                case "float": return double.TryParse(text, NumberStyles.Float, inv, out var d) ? d : 0.0;
                case "bool": return text == "true" || text == "True";
                default: return text ?? "";
            }
        }

        public static string DefaultText(object spec)
        {
            var d = J.Get(spec, "default");
            return d switch
            {
                null => "",
                bool b => b ? "true" : "false",
                double x => x.ToString("R", CultureInfo.InvariantCulture),
                _ => Convert.ToString(d, CultureInfo.InvariantCulture),
            };
        }

        public static bool Required(object spec) => J.Has(spec, "required") ? J.Bool(spec, "required", true) : !J.Has(spec, "default");
    }
}
