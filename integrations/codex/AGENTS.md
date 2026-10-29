# Game art via AI Asset Studio

This project's sprites, 3D props, UI and VFX come from the local AI Asset Studio (MCP server `asset-studio`, skill
`asset-studio`).

- Before making art, call `system_status`; reuse project `<PROJECT NAME>` (`list_projects`) so the style stays locked.
- Make assets with `generate` (or `start_run` + `wait_run`); file inputs are absolute paths.
- Put results into the game with `export_to_engine(engine="<unity|godot>", run_id=…, dest_dir="<abs path to Assets/Art or res://art folder>")`.
- Do not overwrite art folders with `restyle_apply` without the user's go-ahead.
- No MCP in this session? Use the CLI: `studio-core agent --json run <recipe> -i name=value --wait`.
