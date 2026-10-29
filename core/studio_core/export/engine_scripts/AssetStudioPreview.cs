using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace AssetStudio
{
    public static class AssetStudioPreview
    {
        [Serializable]
        class Report
        {
            public bool ok;
            public string target = "";
            public string kind = "";
            public string note = "";
            public List<string> errors = new List<string>();
            public List<string> warnings = new List<string>();
        }

        static Report report = new Report();
        static readonly Color Bg = new Color(0.17f, 0.17f, 0.19f, 1f);

        static void OnLog(string msg, string stack, LogType type)
        {
            if (msg.StartsWith("ASPREVIEW")) return;
            if (type == LogType.Warning) report.warnings.Add(msg);
            else if (type != LogType.Log) report.errors.Add(msg);
        }

        public static void Run()
        {
            var dir = Environment.GetEnvironmentVariable("ASSET_STUDIO_PREVIEW_DIR") ?? "";
            var outPng = Environment.GetEnvironmentVariable("ASSET_STUDIO_PREVIEW_OUT") ?? "";
            var reportPath = Environment.GetEnvironmentVariable("ASSET_STUDIO_PREVIEW_REPORT") ?? "";
            int size;
            if (!int.TryParse(Environment.GetEnvironmentVariable("ASSET_STUDIO_PREVIEW_SIZE"), out size)) size = 640;
            int code = 0;
            Application.logMessageReceived += OnLog;
            try
            {
                AssetDatabase.ImportAsset(dir, ImportAssetOptions.ImportRecursive | ImportAssetOptions.ForceSynchronousImport);
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                RunExportBuilders();
                EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                var root = Spawn(dir);
                var canvas = root != null ? root.GetComponentInChildren<Canvas>() : null;
                if (canvas != null) RenderCanvas(root, canvas, outPng, size);
                else if (root != null) Render(root, outPng, size);
                else if (report.note == "") report.note = "nothing visual to render";
                report.ok = true;
            }
            catch (Exception e)
            {
                report.errors.Add(e.GetType().Name + ": " + e.Message);
                code = 1;
            }
            Application.logMessageReceived -= OnLog;
            if (reportPath != "") File.WriteAllText(reportPath, JsonUtility.ToJson(report));
            Debug.Log("ASPREVIEW DONE code=" + code);
            EditorApplication.Exit(code);
        }

        // Exported postprocessors (modular kit, destructible…) build their prefabs on EditorApplication.delayCall,
        // which never fires before a batchmode -executeMethod exits; call their BuildAll menu entry directly.
        static void RunExportBuilders()
        {
            var builders = AppDomain.CurrentDomain.GetAssemblies().SelectMany(a =>
            {
                try { return a.GetTypes(); }
                catch (System.Reflection.ReflectionTypeLoadException e) { return e.Types.Where(t => t != null); }
            }).Where(t => t.Namespace == "AssetStudio" && typeof(AssetPostprocessor).IsAssignableFrom(t));
            foreach (var t in builders)
            {
                var m = t.GetMethod("BuildAll", System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Static,
                    null, Type.EmptyTypes, null);
                if (m == null) continue;
                m.Invoke(null, null);
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            }
        }

        static string[] Find(string filter, string dir)
        {
            return AssetDatabase.FindAssets(filter, new[] { dir }).Select(AssetDatabase.GUIDToAssetPath).Distinct()
                .OrderBy(p => p.Length).ThenBy(p => p).ToArray();
        }

        static GameObject Spawn(string dir)
        {
            var terrain = SpawnTerrain(dir);
            if (terrain != null) return terrain;
            var prefab = Find("t:Prefab", dir).OrderBy(p => p.EndsWith("_SampleRoom.prefab") ? 0 : 1).FirstOrDefault();
            if (prefab != null)
            {
                report.target = prefab;
                report.kind = "prefab";
                return (GameObject)PrefabUtility.InstantiatePrefab(AssetDatabase.LoadAssetAtPath<GameObject>(prefab));
            }
            var model = Find("t:Model", dir).FirstOrDefault();
            if (model != null)
            {
                report.target = model;
                report.kind = "model";
                return (GameObject)PrefabUtility.InstantiatePrefab(AssetDatabase.LoadAssetAtPath<GameObject>(model));
            }
            var sky = Find("t:Material", dir).Select(AssetDatabase.LoadAssetAtPath<Material>)
                .FirstOrDefault(m => m != null && m.shader != null && m.shader.name.StartsWith("Skybox/"));
            if (sky != null)
            {
                report.target = AssetDatabase.GetAssetPath(sky);
                report.kind = "skybox";
                RenderSettings.skybox = sky;
                return new GameObject("Skybox");
            }
            if (Directory.GetFiles(dir, "*.glb", SearchOption.AllDirectories).Length > 0)
                report.warnings.Add("GLB was not imported as a model (install com.unity.cloud.gltfast)");
            var sprites = Find("t:Sprite", dir).SelectMany(p => AssetDatabase.LoadAllAssetsAtPath(p).OfType<Sprite>())
                .Take(64).ToArray();
            if (sprites.Length > 0)
            {
                report.target = AssetDatabase.GetAssetPath(sprites[0]);
                report.kind = "sprites";
                return SpriteGrid(sprites);
            }
            var tex = Find("t:Texture2D", dir)
                .OrderBy(p => Path.GetFileName(p).ToLowerInvariant().Contains("albedo") || p.ToLowerInvariant().Contains("basecolor") ? 0 : 1)
                .Select(AssetDatabase.LoadAssetAtPath<Texture2D>).FirstOrDefault(t => t != null);
            if (tex != null)
            {
                report.target = AssetDatabase.GetAssetPath(tex);
                report.kind = "texture";
                return TiledQuad(tex);
            }
            if (Find("t:AudioClip", dir).Length > 0) report.note = "audio only";
            return null;
        }

        static GameObject SpriteGrid(Sprite[] sprites)
        {
            var root = new GameObject("Sprites");
            int cols = Mathf.CeilToInt(Mathf.Sqrt(sprites.Length));
            float cell = sprites.Max(s => Mathf.Max(s.bounds.size.x, s.bounds.size.y)) * 1.1f;
            for (int i = 0; i < sprites.Length; i++)
            {
                var go = new GameObject(sprites[i].name);
                go.transform.SetParent(root.transform, false);
                go.transform.localPosition = new Vector3((i % cols) * cell, -(i / cols) * cell, 0) - sprites[i].bounds.center;
                go.AddComponent<SpriteRenderer>().sprite = sprites[i];
            }
            return root;
        }

        static GameObject TiledQuad(Texture2D tex)
        {
            var go = GameObject.CreatePrimitive(PrimitiveType.Quad);
            var mat = new Material(Shader.Find("Unlit/Texture") ?? Shader.Find("Sprites/Default"));
            mat.mainTexture = tex;
            if (tex.wrapMode == TextureWrapMode.Repeat) mat.mainTextureScale = new Vector2(3, 3);
            go.GetComponent<MeshRenderer>().sharedMaterial = mat;
            return go;
        }

        // UI screens are checked the way a phone shows them: 16:9, 20:9 with a notch/home-bar safe area, 4:3 tablet.
        static readonly float[][] UiAspects = { new[] { 9f, 16f, 0f, 0f }, new[] { 9f, 20f, 0.04f, 0.0267f },
            new[] { 3f, 4f, 0f, 0f } };

        static void RenderCanvas(GameObject root, Canvas canvas, string outPng, int size)
        {
            report.kind = "ui";
            MissingRefs(root);
            bool landscape = false;
            var scaler = root.GetComponent("CanvasScaler");
            if (scaler != null)
            {
                var res = scaler.GetType().GetProperty("referenceResolution");
                if (res != null) { var v = (Vector2)res.GetValue(scaler); landscape = v.x > v.y; }
            }
            var safe = root.transform.Find("SafeArea") as RectTransform;
            if (safe != null) foreach (var b in safe.GetComponents<MonoBehaviour>()) b.enabled = false;
            int h = size * 3 / 2, gap = 16;
            var shots = new List<Texture2D>();
            var cam = new GameObject("PreviewCamera").AddComponent<Camera>();
            cam.clearFlags = CameraClearFlags.SolidColor;
            cam.backgroundColor = Bg;
            cam.orthographic = true;
            canvas.renderMode = RenderMode.ScreenSpaceCamera;
            canvas.worldCamera = cam;
            canvas.planeDistance = 10f;
            foreach (var a in UiAspects)
            {
                float ax = landscape ? a[1] : a[0], ay = landscape ? a[0] : a[1];
                int w = Mathf.RoundToInt(h * ax / ay), hh = h;
                if (landscape) { w = h; hh = Mathf.RoundToInt(h * ay / ax); }
                var rt = new RenderTexture(w, hh, 24, RenderTextureFormat.ARGB32) { antiAliasing = 4 };
                cam.targetTexture = rt;
                if (safe != null)
                {
                    float notch = a[2], bar = a[3];
                    safe.anchorMin = landscape ? new Vector2(notch, bar) : new Vector2(0f, bar);
                    safe.anchorMax = landscape ? new Vector2(1f - notch, 1f) : new Vector2(1f, 1f - notch);
                    safe.offsetMin = safe.offsetMax = Vector2.zero;
                }
                if (scaler != null)
                {
                    var handle = scaler.GetType().GetMethod("Handle", System.Reflection.BindingFlags.Instance |
                        System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Public);
                    if (handle != null) handle.Invoke(scaler, null);
                }
                Canvas.ForceUpdateCanvases();
                var label = a[0] + "x" + a[1];
                CheckInside(safe != null ? safe : (RectTransform)root.transform, cam, label);
                cam.Render();
                var prev = RenderTexture.active;
                RenderTexture.active = rt;
                var img = new Texture2D(w, hh, TextureFormat.RGB24, false);
                img.ReadPixels(new Rect(0, 0, w, hh), 0, 0);
                img.Apply();
                RenderTexture.active = prev;
                cam.targetTexture = null;
                shots.Add(img);
            }
            int total = shots.Sum(t => t.width) + gap * (shots.Count + 1), tall = shots.Max(t => t.height) + 2 * gap;
            var sheet = new Texture2D(total, tall, TextureFormat.RGB24, false);
            var fill = Enumerable.Repeat((Color32)Bg, total * tall).ToArray();
            sheet.SetPixels32(fill);
            int x = gap;
            foreach (var t in shots)
            {
                sheet.SetPixels(x, gap, t.width, t.height, t.GetPixels());
                x += t.width + gap;
            }
            sheet.Apply();
            Directory.CreateDirectory(Path.GetDirectoryName(outPng));
            File.WriteAllBytes(outPng, sheet.EncodeToPNG());
            Debug.Log("ASPREVIEW rendered ui " + report.target + " aspects=" + shots.Count);
        }

        static void CheckInside(RectTransform safe, Camera cam, string label)
        {
            var sc = ScreenRect(safe, cam);
            foreach (var rt in safe.GetComponentsInChildren<RectTransform>(true))
            {
                if (rt == safe) continue;
                var r = ScreenRect(rt, cam);
                var p = rt.parent as RectTransform;
                var pr = p != null ? ScreenRect(p, cam) : sc;
                if (r.xMin < Mathf.Max(sc.xMin, pr.xMin) - 1f || r.yMin < Mathf.Max(sc.yMin, pr.yMin) - 1f ||
                    r.xMax > Mathf.Min(sc.xMax, pr.xMax) + 1f || r.yMax > Mathf.Min(sc.yMax, pr.yMax) + 1f)
                    report.errors.Add("UI overflow " + label + ": " + rt.name);
            }
        }

        static Rect ScreenRect(RectTransform rt, Camera cam)
        {
            var c = new Vector3[4];
            rt.GetWorldCorners(c);
            var a = RectTransformUtility.WorldToScreenPoint(cam, c[0]);
            var b = RectTransformUtility.WorldToScreenPoint(cam, c[2]);
            return Rect.MinMaxRect(Mathf.Min(a.x, b.x), Mathf.Min(a.y, b.y), Mathf.Max(a.x, b.x), Mathf.Max(a.y, b.y));
        }

        static void MissingRefs(GameObject root)
        {
            foreach (var t in root.GetComponentsInChildren<Transform>(true))
            {
                if (GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(t.gameObject) > 0)
                    report.errors.Add("Missing script on " + t.name);
                foreach (var c in t.GetComponents<Component>())
                {
                    if (c == null) continue;
                    var it = new SerializedObject(c).GetIterator();
                    while (it.Next(true))
                        if (it.propertyType == SerializedPropertyType.ObjectReference && it.objectReferenceValue == null
                            && it.objectReferenceInstanceIDValue != 0)
                            report.errors.Add("Missing reference " + t.name + "." + c.GetType().Name + "." + it.propertyPath);
                }
            }
        }

        // A terrain export builds its TerrainData through its own Editor script (export/terrain.py), which only runs
        // after import in the Editor; batch preview calls it directly, by name so this file compiles without it.
        static GameObject SpawnTerrain(string dir)
        {
            var json = Directory.GetFiles(dir, "*_terrain.json", SearchOption.AllDirectories).FirstOrDefault();
            if (json == null) return null;
            var type = AppDomain.CurrentDomain.GetAssemblies().Select(a => a.GetType("AssetStudio.AssetStudioTerrain"))
                .FirstOrDefault(t => t != null);
            if (type == null)
            {
                report.warnings.Add("terrain json without its AssetStudioTerrain editor script");
                return null;
            }
            var path = json.Replace('\\', '/');
            var prefab = (GameObject)type.GetMethod("Build").Invoke(null, new object[] { path, false });
            report.target = path;
            report.kind = "terrain";
            var go = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
            // The far-away base map is baked lazily by the Editor loop, which batch mode never runs.
            foreach (var t in go.GetComponentsInChildren<Terrain>()) t.basemapDistance = 1e6f;
            return go;
        }

        static void Render(GameObject root, string outPng, int size)
        {
            // A freshly spawned particle prefab has no particles yet; one-shot effects peak within ~0.3 s.
            foreach (var ps in root.GetComponentsInChildren<ParticleSystem>())
                if (ps.transform.parent == null || ps.transform.parent.GetComponentInParent<ParticleSystem>() == null)
                    ps.Simulate(0.3f, true, true);
            bool particles = root.GetComponentInChildren<ParticleSystem>() != null;
            var renderers = root.GetComponentsInChildren<Renderer>().Where(r => r.enabled).ToArray();
            var terrains = root.GetComponentsInChildren<Terrain>();
            var b = renderers.Length > 0 ? renderers[0].bounds : new Bounds(root.transform.position, Vector3.one);
            foreach (var r in renderers) b.Encapsulate(r.bounds);
            foreach (var t in terrains)
            {
                var tb = t.terrainData.bounds;
                tb.center += t.transform.position;
                if (renderers.Length == 0 && t == terrains[0]) b = tb;
                else b.Encapsulate(tb);
            }
            bool flat = terrains.Length == 0 && (renderers.Length == 0 || renderers.All(r => r is SpriteRenderer) || b.size.z < 1e-4f || particles);
            var cam = new GameObject("PreviewCamera").AddComponent<Camera>();
            cam.clearFlags = CameraClearFlags.SolidColor;
            cam.backgroundColor = Bg;
            cam.aspect = 1f;
            if (report.kind == "skybox")
            {
                cam.clearFlags = CameraClearFlags.Skybox;
                cam.fieldOfView = 90f;
                cam.transform.rotation = Quaternion.Euler(-10f, 0f, 0f);
            }
            else if (flat)
            {
                cam.orthographic = true;
                cam.orthographicSize = Mathf.Max(b.extents.x, b.extents.y, 0.01f) * 1.1f;
                cam.transform.position = b.center - Vector3.forward * (b.extents.z + 10f);
                cam.nearClipPlane = 0.01f;
                cam.farClipPlane = b.size.z + 100f;
            }
            else
            {
                float radius = Mathf.Max(b.extents.magnitude, 0.01f);
                cam.fieldOfView = 35f;
                var dir = new Vector3(0.8f, 0.55f, -1f).normalized;
                cam.transform.position = b.center + dir * (radius / Mathf.Sin(cam.fieldOfView * 0.5f * Mathf.Deg2Rad) * 1.05f);
                cam.transform.LookAt(b.center);
                cam.nearClipPlane = radius * 0.01f;
                cam.farClipPlane = radius * 100f;
                var sun = new GameObject("PreviewLight").AddComponent<Light>();
                sun.type = LightType.Directional;
                sun.transform.rotation = Quaternion.Euler(50f, -30f, 0f);
                RenderSettings.ambientLight = new Color(0.55f, 0.55f, 0.6f);
            }
            var rt = new RenderTexture(size, size, 24, RenderTextureFormat.ARGB32) { antiAliasing = 4 };
            cam.targetTexture = rt;
            cam.Render();
            var prev = RenderTexture.active;
            RenderTexture.active = rt;
            var img = new Texture2D(size, size, TextureFormat.RGB24, false);
            img.ReadPixels(new Rect(0, 0, size, size), 0, 0);
            img.Apply();
            RenderTexture.active = prev;
            cam.targetTexture = null;
            Directory.CreateDirectory(Path.GetDirectoryName(outPng));
            File.WriteAllBytes(outPng, img.EncodeToPNG());
            Debug.Log("ASPREVIEW rendered " + report.kind + " " + report.target + " renderers=" + renderers.Length);
        }
    }
}
