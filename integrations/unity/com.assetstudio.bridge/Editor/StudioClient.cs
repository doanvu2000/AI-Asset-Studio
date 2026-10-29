using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Threading.Tasks;

namespace AssetStudio.Bridge
{
    public sealed class StudioException : Exception
    {
        public readonly int Status;
        public readonly string Code;

        public StudioException(int status, string code, string message) : base($"{code}: {message}")
        {
            Status = status;
            Code = code;
        }
    }

    /// <summary>Where the plugin looks for AI Asset Studio: settings written by the app's installer, then defaults.</summary>
    public static class StudioLocator
    {
        public const string LockName = "core.lock.json";
        public const string ConfigFile = "UserSettings/AssetStudioBridge.json";
        public const string AppName = "AI Asset Studio";
        const string DataDirPref = "AssetStudio.Bridge.DataDir";

        public static string DataDirOverride
        {
            get => UnityEditor.EditorPrefs.GetString(DataDirPref, "");
            set => UnityEditor.EditorPrefs.SetString(DataDirPref, value ?? "");
        }

        public static Dictionary<string, object> InstallConfig()
        {
            try
            {
                return File.Exists(ConfigFile) ? MiniJson.Parse(File.ReadAllText(ConfigFile)) as Dictionary<string, object> : null;
            }
            catch (Exception)
            {
                return null;
            }
        }

        public static List<string> CandidateDataDirs()
        {
            var dirs = new List<string>();
            void Add(string d)
            {
                if (string.IsNullOrWhiteSpace(d)) return;
                var full = Path.GetFullPath(d);
                if (!dirs.Contains(full)) dirs.Add(full);
            }
            Add(DataDirOverride);
            Add(J.Str(InstallConfig(), "data_dir"));
            Add(Environment.GetEnvironmentVariable("STUDIO_DATA_DIR"));
            Add(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), AppName, "data"));
            return dirs;
        }

        public static string AppExe()
        {
            var configured = J.Str(InstallConfig(), "app_exe");
            if (TrustedExe(configured)) return configured;
            var local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            foreach (var root in new[] { Path.Combine(local, "Programs"), Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles) })
            {
                var exe = Path.Combine(root, AppName, AppName + ".exe");
                if (File.Exists(exe)) return exe;
            }
            return null;
        }

        // The install config sits in the project, so a cloned project must not be able to point "Open app" at its own exe.
        static bool TrustedExe(string exe)
        {
            if (string.IsNullOrEmpty(exe) || exe.StartsWith(@"\\") || !File.Exists(exe)) return false;
            if (!string.Equals(Path.GetFileName(exe), AppName + ".exe", StringComparison.OrdinalIgnoreCase)) return false;
            var project = Path.GetFullPath(Path.Combine(UnityEngine.Application.dataPath, "..")) + Path.DirectorySeparatorChar;
            return !Path.GetFullPath(exe).StartsWith(project, StringComparison.OrdinalIgnoreCase);
        }

        public static bool OpenApp()
        {
            var exe = AppExe();
            if (exe == null) return false;
            Process.Start(new ProcessStartInfo(exe) { UseShellExecute = true, WorkingDirectory = Path.GetDirectoryName(exe) });
            return true;
        }

        internal static Dictionary<string, object> ReadLock(string dataDir)
        {
            try
            {
                var path = Path.Combine(dataDir, LockName);
                if (!File.Exists(path)) return null;
                var info = MiniJson.Parse(File.ReadAllText(path)) as Dictionary<string, object>;
                if (info == null || !(J.Get(info, "port") is long) || string.IsNullOrEmpty(J.Str(info, "token"))) return null;
                return info;
            }
            catch (Exception)
            {
                return null;
            }
        }

        internal static bool PidAlive(long pid)
        {
            if (pid <= 0) return false;
            Process p;
            try
            {
                p = Process.GetProcessById((int)pid);
            }
            catch (Exception)
            {
                return false;
            }
            // Mono may deny HasExited on a process it did not start; the token check that follows rules out pid reuse.
            try
            {
                return !p.HasExited;
            }
            catch (Exception)
            {
                return true;
            }
            finally
            {
                p.Dispose();
            }
        }

        internal static string LockHost(Dictionary<string, object> info)
        {
            // The lock path can come from a project-controlled config; never send its token off this machine.
            return J.Str(info, "host") == "localhost" ? "localhost" : "127.0.0.1";
        }
    }

    /// <summary>HTTP client for the local core, found through `core.lock.json` (same contract as the MCP CoreLink).</summary>
    public sealed class StudioClient : IDisposable
    {
        readonly HttpClient http;
        public readonly string BaseUrl;
        public readonly string DataDir;

        StudioClient(string baseUrl, string token, string dataDir)
        {
            BaseUrl = baseUrl;
            DataDir = dataDir;
            http = new HttpClient { BaseAddress = new Uri(baseUrl), Timeout = TimeSpan.FromMinutes(15) };
            http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
        }

        public override string ToString() => $"AI Asset Studio core {BaseUrl}";

        /// <summary>A live core from the first data dir whose lock answers with its token, or null.</summary>
        /// <param name="dataDirs">Pass these off the main thread: the default list reads EditorPrefs.</param>
        public static async Task<StudioClient> ConnectAsync(List<string> dataDirs = null)
        {
            foreach (var dir in dataDirs ?? StudioLocator.CandidateDataDirs())
            {
                var info = StudioLocator.ReadLock(dir);
                if (info == null || !StudioLocator.PidAlive((long)J.Num(info, "pid"))) continue;
                var c = new StudioClient($"http://{StudioLocator.LockHost(info)}:{(long)J.Num(info, "port")}",
                    J.Str(info, "token"), dir);
                if (await c.AliveAsync()) return c;
                c.Dispose();
            }
            return null;
        }

        public async Task<bool> AliveAsync()
        {
            try
            {
                using var r = await http.GetAsync("/api/runs?limit=1").ConfigureAwait(false);
                return r.IsSuccessStatusCode;
            }
            catch (Exception)
            {
                return false;
            }
        }

        public Task<object> GetAsync(string path) => SendAsync(HttpMethod.Get, path, null);
        public Task<object> PostAsync(string path, object body = null) => SendAsync(HttpMethod.Post, path, body);
        public Task<object> PatchAsync(string path, object body) => SendAsync(new HttpMethod("PATCH"), path, body);

        async Task<object> SendAsync(HttpMethod method, string path, object body)
        {
            using var req = new HttpRequestMessage(method, path);
            if (body != null || method != HttpMethod.Get)
                req.Content = new StringContent(MiniJson.Serialize(body ?? new Dictionary<string, object>()), Encoding.UTF8, "application/json");
            return await ReadAsync(await Do(req).ConfigureAwait(false)).ConfigureAwait(false);
        }

        async Task<HttpResponseMessage> Do(HttpRequestMessage req)
        {
            try
            {
                return await http.SendAsync(req).ConfigureAwait(false);
            }
            catch (Exception e) when (e is HttpRequestException or TaskCanceledException)
            {
                throw new StudioException(0, "core_unreachable", e.GetType().Name + ": " + e.Message);
            }
        }

        static async Task<object> ReadAsync(HttpResponseMessage r)
        {
            using (r)
            {
                var text = r.Content == null ? "" : await r.Content.ReadAsStringAsync().ConfigureAwait(false);
                if (!r.IsSuccessStatusCode)
                {
                    object err = null;
                    try { err = J.Obj(MiniJson.Parse(text), "error"); } catch (FormatException) { }
                    var msg = J.Str(err, "message", text.Length > 500 ? text.Substring(0, 500) : text);
                    throw new StudioException((int)r.StatusCode, J.Str(err, "code", "http_error"), msg);
                }
                return string.IsNullOrEmpty(text) ? null : MiniJson.Parse(text);
            }
        }

        /// <summary>Uploads a local file to the blob store; returns its hash.</summary>
        public async Task<string> UploadAsync(string filePath)
        {
            // Owned by the request: Mono's MultipartContent throws when disposed twice.
            var form = new MultipartFormDataContent();
            var bytes = File.ReadAllBytes(filePath);
            var part = new ByteArrayContent(bytes);
            part.Headers.ContentType = new MediaTypeHeaderValue("application/octet-stream");
            form.Add(part, "file", Path.GetFileName(filePath));
            using var req = new HttpRequestMessage(HttpMethod.Post, "/api/blobs") { Content = form };
            var res = await ReadAsync(await Do(req).ConfigureAwait(false)).ConfigureAwait(false);
            return J.Str(res, "hash");
        }

        public async Task<byte[]> GetBytesAsync(string path)
        {
            using var req = new HttpRequestMessage(HttpMethod.Get, path);
            using var r = await Do(req).ConfigureAwait(false);
            if (!r.IsSuccessStatusCode) throw new StudioException((int)r.StatusCode, "download_failed", path);
            return await r.Content.ReadAsByteArrayAsync().ConfigureAwait(false);
        }

        public void Dispose() => http.Dispose();
    }
}
