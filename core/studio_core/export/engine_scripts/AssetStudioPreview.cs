using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

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
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var root = Spawn(dir);
            if (root != null) Render(root, outPng, size);
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

    static string[] Find(string filter, string dir)
    {
        return AssetDatabase.FindAssets(filter, new[] { dir }).Select(AssetDatabase.GUIDToAssetPath).Distinct()
            .OrderBy(p => p.Length).ThenBy(p => p).ToArray();
    }

    static GameObject Spawn(string dir)
    {
        var prefab = Find("t:Prefab", dir).FirstOrDefault();
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
        var tex = Find("t:Texture2D", dir).Select(AssetDatabase.LoadAssetAtPath<Texture2D>).FirstOrDefault(t => t != null);
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
        go.transform.rotation = Quaternion.Euler(0, 180, 0);
        return go;
    }

    static void Render(GameObject root, string outPng, int size)
    {
        var renderers = root.GetComponentsInChildren<Renderer>();
        var b = renderers.Length > 0 ? renderers[0].bounds : new Bounds(root.transform.position, Vector3.one);
        foreach (var r in renderers) b.Encapsulate(r.bounds);
        bool flat = renderers.Length == 0 || renderers.All(r => r is SpriteRenderer) || b.size.z < 1e-4f;
        var cam = new GameObject("PreviewCamera").AddComponent<Camera>();
        cam.clearFlags = CameraClearFlags.SolidColor;
        cam.backgroundColor = Bg;
        cam.aspect = 1f;
        if (flat)
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
