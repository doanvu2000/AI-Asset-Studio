# AI Asset Studio Bridge (Unity)

Editor-only package to run AI Asset Studio recipes from Unity 2021.3+ (tested on 6000.3):

- **Window → AI Asset Studio**: recipe + project pickers, input form generated from the core's recipe spec,
  step progress, gate review (pick / approve), outputs imported into `Assets/AssetStudio/<recipe>/` through the
  studio's Unity exporter (prefabs, sprite meta, materials) or as raw files when a step has no engine export.
- **Project window right-click → AI Asset Studio**: Restyle (folder), Tô lại texture (.glb/.fbx/.obj),
  Biến thể kẻ địch (sprite sheet + its .json), Thêm vào Dự án (image becomes the project's style reference).
- **Batch**: `Unity -batchmode -projectPath P -executeMethod AssetStudio.Bridge.BridgeBatch.Run -asRecipe concept-2d
  -asInput "prompt=red potion" [-asInput k=v] [-asProject id] [-asTimeout 1800] [-asGate fail]` (gates auto-pick first).

Install from the app (Cài đặt → Công cụ → Cài plugin vào project…): it copies this folder to `Packages/` and writes
`UserSettings/AssetStudioBridge.json` (data folder + app path, no token). The package finds the running core through
`<data>/core.lock.json`; if AI Asset Studio is closed, the window offers "Mở AI Asset Studio".
Tests: add `"testables": ["com.assetstudio.bridge"]` to `Packages/manifest.json`; live tests skip when no core runs.
