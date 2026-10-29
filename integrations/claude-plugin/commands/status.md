---
description: Show AI Asset Studio status (core, ComfyUI, Blender, GPU queue, open gates, recent runs)
allowed-tools: mcp__asset-studio, mcp__plugin_asset-studio_asset-studio
---
Call system_status and list_runs(limit=10). Summarise in a few lines: services up/down, what each lane (gpu,
blender) is busy with, open gates waiting for the user, missing model packs, failed runs worth retrying.
