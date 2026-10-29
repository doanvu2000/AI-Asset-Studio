using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Threading.Tasks;
using UnityEditor;
using Debug = UnityEngine.Debug;

namespace AssetStudio.Bridge
{
    /// <summary>
    /// Headless entry: Unity -batchmode -executeMethod AssetStudio.Bridge.BridgeBatch.Run -asRecipe concept-2d
    /// -asInput prompt="red potion" [-asInput k=v ...] [-asProject id] [-asTimeout 1800] [-asGate auto_first|fail].
    /// Gates are auto-resolved (first candidates, approve) unless -asGate fail. Outputs land in Assets/AssetStudio.
    /// </summary>
    public static class BridgeBatch
    {
        public static void Run()
        {
            var code = 1;
            try
            {
                var args = Environment.GetCommandLineArgs();
                string Arg(string name) => Enumerable.Range(0, args.Length - 1).Where(i => args[i] == name).Select(i => args[i + 1]).LastOrDefault();
                var recipe = Arg("-asRecipe") ?? throw new ArgumentException("-asRecipe is required");
                var inputs = Enumerable.Range(0, args.Length - 1).Where(i => args[i] == "-asInput").Select(i => args[i + 1])
                    .Select(kv => kv.Split(new[] { '=' }, 2)).ToDictionary(p => p[0], p => p.Length > 1 ? p[1] : "");
                var timeout = double.TryParse(Arg("-asTimeout"), out var t) ? t : 1800;
                var dataDirs = StudioLocator.CandidateDataDirs();
                var dirs = Task.Run(() => RunAsync(dataDirs, recipe, inputs, Arg("-asProject"), timeout, Arg("-asGate") != "fail"))
                    .GetAwaiter().GetResult();
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                foreach (var d in dirs) Debug.Log($"[AssetStudio] imported {d}");
                code = dirs.Count > 0 ? 0 : 2;
            }
            catch (Exception e)
            {
                Debug.LogError($"[AssetStudio] {e.GetType().Name}: {e.Message}");
            }
            EditorApplication.Exit(code);
        }

        /// <summary>Thread-pool safe (no Unity API): start, follow, auto-resolve gates, import outputs.</summary>
        public static async Task<List<string>> RunAsync(List<string> dataDirs, string recipeId, Dictionary<string, string> textInputs,
            string projectId, double timeoutS, bool autoGates, string source = "unity")
        {
            using var c = await StudioClient.ConnectAsync(dataDirs) ?? throw new StudioException(0, "core_not_found",
                "AI Asset Studio is not running (no live core.lock.json in the known data dirs)");
            var recipe = (await StudioRuns.RecipesAsync(c)).FirstOrDefault(r => J.Str(r, "id") == recipeId)
                         ?? throw new StudioException(404, "unknown_recipe", recipeId);
            var specs = J.Obj(recipe, "inputs") ?? new Dictionary<string, object>();
            var unknown = textInputs.Keys.Where(k => !specs.ContainsKey(k)).ToList();
            if (unknown.Count > 0) throw new StudioException(400, "unknown_inputs", string.Join(", ", unknown));
            var inputs = textInputs.ToDictionary(kv => kv.Key, kv =>
                StudioRuns.BlobTypes.Contains(J.Str(specs[kv.Key], "type")) ? kv.Value : StudioRuns.ParseValue(specs[kv.Key], kv.Value));
            var runId = await StudioRuns.StartAsync(c, recipe, inputs, projectId, $"Unity batch: {recipeId}", source);
            Debug.Log($"[AssetStudio] run {runId} started ({recipeId})");
            var clock = Stopwatch.StartNew();
            var last = "";
            while (true)
            {
                var (run, steps) = await StudioRuns.GetRunAsync(c, runId);
                var status = J.Str(run, "status");
                if (status != last) Debug.Log($"[AssetStudio] {runId} {status} after {clock.Elapsed.TotalSeconds:0}s");
                last = status;
                if (status == "awaiting_human")
                {
                    if (!autoGates) throw new StudioException(0, "gate", "run is waiting at a gate (-asGate fail)");
                    if (await StudioRuns.AutoResolveAsync(c, steps)) continue;
                }
                if (status == "succeeded") break;
                if (StudioRuns.IsTerminal(status))
                {
                    var failed = steps.FirstOrDefault(s => J.Obj(s, "error") != null);
                    throw new StudioException(0, status, failed == null ? status : $"{J.Str(failed, "key")}: {J.Str(J.Obj(failed, "error"), "message")}");
                }
                if (clock.Elapsed.TotalSeconds > timeoutS) throw new StudioException(0, "timeout", $"run {runId} not finished after {timeoutS:0}s");
                await Task.Delay(1000);
            }
            return await StudioRuns.ImportOutputsAsync(c, runId);
        }
    }
}
