using System.Collections.Generic;
using System.IO;
using UnityEditor;

namespace AssetStudio.Bridge
{
    static class AssetStudioMenu
    {
        const string Root = "Assets/AI Asset Studio/";
        const int Order = 2100;
        static readonly string[] MeshExt = { ".glb", ".fbx", ".obj" };
        static readonly string[] ImageExt = { ".png", ".jpg", ".jpeg", ".webp" };

        static string Selected => Selection.activeObject == null ? null : AssetDatabase.GetAssetPath(Selection.activeObject);
        static string Full(string assetPath) => Path.GetFullPath(assetPath);
        static bool HasExt(string p, string[] exts) => p != null && System.Array.IndexOf(exts, Path.GetExtension(p).ToLowerInvariant()) >= 0;

        [MenuItem(Root + "Restyle", false, Order)]
        static void Restyle()
        {
            var p = Selected;
            var folder = AssetDatabase.IsValidFolder(p) ? p : Path.GetDirectoryName(p);
            AssetStudioWindow.Open("restyle-folder", new Dictionary<string, string> { ["folder"] = Full(folder) });
        }

        [MenuItem(Root + "Restyle", true)]
        static bool RestyleValid() => !string.IsNullOrEmpty(Selected) && Selected.StartsWith("Assets");

        [MenuItem(Root + "Tô lại texture", false, Order + 1)]
        static void Retexture() => AssetStudioWindow.Open("mesh-retexture", new Dictionary<string, string> { ["mesh"] = Full(Selected) });

        [MenuItem(Root + "Tô lại texture", true)]
        static bool RetextureValid() => HasExt(Selected, MeshExt);

        /// <summary>The sheet's .json (same stem, or `_sheet.json`) rides along when it sits next to the PNG.</summary>
        [MenuItem(Root + "Biến thể kẻ địch", false, Order + 2)]
        static void EnemyVariants()
        {
            var png = Full(Selected);
            var prefill = new Dictionary<string, string> { ["sheet"] = png };
            var json = Path.ChangeExtension(png, ".json");
            if (File.Exists(json)) prefill["meta"] = json;
            AssetStudioWindow.Open("enemy-variants", prefill);
        }

        [MenuItem(Root + "Biến thể kẻ địch", true)]
        static bool EnemyVariantsValid() => HasExt(Selected, ImageExt);

        [MenuItem(Root + "Thêm vào Dự án", false, Order + 3)]
        static void AddToProject() => AssetStudioWindow.OpenAddToProject(Full(Selected));

        [MenuItem(Root + "Thêm vào Dự án", true)]
        static bool AddToProjectValid() => !string.IsNullOrEmpty(Selected) && !AssetDatabase.IsValidFolder(Selected);

        [MenuItem(Root + "Mở cửa sổ", false, Order + 20)]
        static void OpenWindow() => AssetStudioWindow.ShowWindow();
    }
}
