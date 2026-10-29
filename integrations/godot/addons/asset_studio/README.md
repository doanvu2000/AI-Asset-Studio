# AI Asset Studio — Godot addon

Dock (right panel) to run AI Asset Studio recipes from the Godot 4.4+ editor: recipe form generated from the core's
recipe spec, progress, gate review (pick / approve) and output import into `res://asset_studio/<recipe>/` through the
studio's Godot exporter. FileSystem right-click: Restyle, Tô lại texture, Biến thể kẻ địch, Thêm vào Dự án.

Install from the app (Cài đặt → Công cụ → Cài plugin vào project…), or copy `addons/asset_studio` into the project
and enable it in Project Settings → Plugins. The app writes `bridge_config.json` (data folder + app path, no token).
The addon finds the running core through `<data>/core.lock.json`; editor setting `asset_studio/data_dir` overrides.
