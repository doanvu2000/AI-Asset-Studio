---
description: Package a project's approved assets for Unity/Godot (QA first)
argument-hint: <project name or id> [unity|godot] [atlas] [mobile]
allowed-tools: mcp__asset-studio, mcp__plugin_asset-studio_asset-studio
---
Use the asset-studio skill. Target: $ARGUMENTS

1. Resolve the project (list_projects). Run project_qa (flagged_only) and show the worst issues; ask before fixing.
2. build_asset_pack(project_id, engines=…, atlas/mobile as asked). Report the pack folder/zip paths, the version and
   any assets flagged for non-commercial model licenses.
