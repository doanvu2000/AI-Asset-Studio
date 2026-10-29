---
description: Make a game asset with AI Asset Studio (picks the recipe, runs it, returns file paths)
argument-hint: <what to make, e.g. "8-direction knight sprite for project Dungeon">
allowed-tools: mcp__asset-studio, mcp__plugin_asset-studio_asset-studio
---
Use the asset-studio skill. Request: $ARGUMENTS

1. Call system_status; if ComfyUI/Blender or a model pack the recipe needs is missing, say so before starting.
2. Find the user's project (list_projects) or ask whether to create one; pass its project_id.
3. Choose the recipe from the catalog (describe_recipe to confirm inputs). Prompts in English.
4. Run with generate (or start_run + wait_run with gate_policy "agent" if the user wants you to choose candidates).
5. Report the output file paths, the Library version ids, and useful follow_up_options.
