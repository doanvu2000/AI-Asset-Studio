using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using NUnit.Framework;
using UnityEngine;

namespace AssetStudio.Bridge.Tests
{
    public class BridgeTests
    {
        [Test]
        public void MiniJsonRoundTrips()
        {
            var src = "{\"a\":[1,2.5,\"x\\n\\u00e9\"],\"b\":{\"c\":true,\"d\":null},\"e\":-3}";
            var v = MiniJson.Parse(src);
            Assert.AreEqual(1L, J.Arr(v, "a")[0]);
            Assert.AreEqual(2.5, J.Arr(v, "a")[1]);
            Assert.AreEqual("x\né", J.Arr(v, "a")[2]);
            Assert.IsTrue(J.Bool(J.Obj(v, "b"), "c"));
            Assert.AreEqual(src.Replace("\\u00e9", "é"), MiniJson.Serialize(v));
        }

        [Test]
        public void AtlasBringsItsPages()
        {
            var dir = Path.Combine(Path.GetTempPath(), "asb-" + System.Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(dir);
            try
            {
                var atlas = Path.Combine(dir, "hero.atlas");
                File.WriteAllText(atlas, "hero.png\nsize: 64,64\nfilter: Linear,Linear\nhead\n  bounds: 0,0,8,8\nhero2.png\nsize: 64,64\n");
                File.WriteAllBytes(Path.Combine(dir, "hero.png"), new byte[] { 1 });
                File.WriteAllBytes(Path.Combine(dir, "hero2.png"), new byte[] { 1 });
                var spec = new Dictionary<string, object> { ["type"] = "file", ["siblings"] = "atlas_pages" };
                var files = StudioRuns.WithSiblings(spec, atlas).Select(Path.GetFileName).ToArray();
                CollectionAssert.AreEqual(new[] { "hero.atlas", "hero.png", "hero2.png" }, files);
            }
            finally
            {
                Directory.Delete(dir, true);
            }
        }

        static StudioClient Connect()
        {
            var dirs = StudioLocator.CandidateDataDirs();
            var c = Task.Run(() => StudioClient.ConnectAsync(dirs)).GetAwaiter().GetResult();
            if (c == null) Assert.Ignore("AI Asset Studio core is not running; live API tests skipped. Looked in: " + string.Join("; ", dirs));
            return c;
        }

        [Test]
        public void LiveCoreListsRecipesAndProjects()
        {
            using var c = Connect();
            var recipes = Task.Run(() => StudioRuns.RecipesAsync(c)).GetAwaiter().GetResult();
            var concept = recipes.FirstOrDefault(r => J.Str(r, "id") == "concept-2d");
            Assert.IsNotNull(concept, "concept-2d recipe");
            Assert.AreEqual("text", J.Str(J.Obj(J.Obj(concept, "inputs"), "prompt"), "type"));
            Assert.IsNotNull(Task.Run(() => StudioRuns.ProjectsAsync(c)).GetAwaiter().GetResult());
        }

        [Test]
        public void LiveCoreTakesAnUpload()
        {
            using var c = Connect();
            var tex = new Texture2D(8, 8);
            var pixels = Enumerable.Repeat(new Color32(200, 40, 40, 255), 64).ToArray();
            tex.SetPixels32(pixels);
            var png = Path.Combine(Path.GetTempPath(), "asb-upload-test.png");
            File.WriteAllBytes(png, tex.EncodeToPNG());
            Object.DestroyImmediate(tex);
            try
            {
                var hash = Task.Run(() => c.UploadAsync(png)).GetAwaiter().GetResult();
                StringAssert.IsMatch("^[0-9a-f]{64}$", hash);
                var info = Task.Run(() => c.GetAsync($"/api/blobs/{hash}/info")).GetAwaiter().GetResult();
                Assert.AreEqual("image/png", J.Str(info, "mime"));
            }
            finally
            {
                File.Delete(png);
            }
        }

        [Test]
        public void UnknownRunIsA404()
        {
            using var c = Connect();
            var e = Assert.Throws<StudioException>(() => Task.Run(() => c.GetAsync("/api/runs/does-not-exist")).GetAwaiter().GetResult());
            Assert.AreEqual(404, e.Status);
        }
    }
}
