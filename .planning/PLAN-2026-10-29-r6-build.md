# PLAN — làm #45–#55 (lượt 6) — 2026-10-29

Chủ nhân: "làm cả, lập spec chi tiết, chia sub agent tự làm + review + build test; con giữ handoff/progress/commit".
Thêm #55: Claude CLI / Codex CLI tự điều khiển AI-Asset-Studio qua MCP + skill + plugin.
Ý tưởng gốc: `.planning/PLAN-2026-10-29-feature-ideas-r6.md`.

## Luật chung cho mọi agent
- Cùng working tree `L:\AI\AI-Asset-Studio` (không worktree). Mỗi agent 1 nhóm chức năng, chỉ sửa file của mình.
- File dùng chung (engine/kinds.py, recipe registry, api/routes.py, mcp/server.py, app art/index.ts, followUp.ts, BundleMenu,
  `packaging/studio-core.spec`, `store/model_licenses.py`): chỉ thêm, đọc lại ngay trước khi Edit, không format lại file.
  `app/src/composables/changelog.ts` và `.planning/*` để con chính (không agent nào sửa).
- GPU: `mkdir L:/AI/AI-Asset-Studio/.gpu-lock` (atomic) trước mọi live run ComfyUI/Blender GPU; đang có khoá → poll 30s;
  xong xoá khoá **mình** tạo. Không giết ComfyUI/Blender của agent khác. Live run giữ ngắn (1 lượt đại diện).
- Không git commit/push. Không `npm run build/dist` (agent tích hợp cuối mới build). Được chạy `cd core && uv run python -m pytest -q tests`
  (hoặc test file của mình), `cd app && npm run typecheck`.
- Comment ít: chỉ "tại sao" (workaround, ràng buộc SDK/engine, magic number có nguồn). Không header trang trí, không đánh số bước.
- Unity: CLI `L:\setup\unity\bin\unity.exe` (Editor 6000.3.27f1), project test `L:\AI\unity-test-batch` (Built-in) /
  `L:\AI\unity-urp-shadercheck` (URP); skill `unity:unity-cli`. Godot tự dò (`config.find_godot`); `--import` headless có lúc
  không tự thoát → đặt timeout. Không đụng `L:\AI\AI Asset Studio Portable\data`.
- Project / thư mục test tự tạo thì xoá khi xong (trừ ảnh QA nhỏ để trong scratchpad của mình). Không để file rác trong `core/`.
- Không model mới (cả lượt). Không audio. Không cloud.
- Mỗi chức năng xong khi: recipe yaml + op/step + exporter Unity/Godot + lối vào UI (thẻ recipe + art thẻ, FollowUpMenu nếu hợp)
  + test (fake executor cho Comfy/Blender) + pytest toàn bộ pass + typecheck sạch + 1 live run thật (hoặc ghi rõ vì sao không)
  + kiểm mở trong Unity/Godot. File mới Blender/engine script phải thêm vào `packaging/studio-core.spec` nếu spec liệt kê thủ công.
- Báo cáo cuối (≤40 dòng): file đã thêm/sửa, số test, kết quả live (thời gian, VRAM, số liệu nghiệm thu), nợ còn lại,
  dòng changelog đề xuất (tiếng Việt, 1 câu).

## Phân công
| Agent | # | Recipe mới (gợi ý id) | GPU |
|---|---|---|---|
| A | 49 + 51 | `item-spin`, `ui-juice`; `spine-physics` (hoặc bước trong spine-character) | Blender (turntable) |
| B | 52 + 54 | API/màn `/audit` (không phải recipe); `minimap` | Klein (54) |
| C | 48 | `vfx-particles` | Z-Image/Klein |
| D | 45 + 46 | `trim-sheet`, `decal-pack`, `modular-kit` | Z-Image, Blender |
| E | 47 | `terrain-3d` | Z-Image |
| F | 50 | `ui-screen` | Klein/Z-Image (mockup) |
| G | 53 | (không recipe) Unity package + Godot addon client | không |
| H | 55 | MCP mở rộng + skill + plugin Claude/Codex + CLI | không |
| R | — | sau cùng: review toàn bộ diff + fix, pytest, typecheck, build dist, e2e core đóng gói | các recipe mới |

## Spec từng mục

### #49 item-spin + ui-juice (A)
- `item-spin`: input mesh (GLB từ prop-3d) **hoặc** ảnh 2D. Mesh → Blender turntable quanh trục Y, 12/16/24 frame, đèn 3 điểm,
  ortho, nền trong; ảnh 2D → giả xoay bằng co giãn ngang theo cos + đổi sáng mặt sau (coin phẳng), tuỳ chọn độ dày viền.
- `ui-juice`: ảnh UI/icon → loop shine sweep (dải sáng chéo qua mask alpha), pulse glow (outer glow theo alpha), bob/float,
  sparkle (sao lấp lánh ngẫu nhiên có seed). CPU thuần (Pillow/numpy).
- Xuất: sheet + anim Unity (AnimationClip sprite) / Godot SpriteFrames (dùng exporter sheet có sẵn), GIF/WebP preview.
- Nghiệm thu: frame cuối→đầu liền (diff nhỏ hơn diff giữa 2 frame liên tiếp), pivot lệch ≤1px, alpha không viền đen.

### #51 spine-physics (A)
- Sau auto-rig Spine (spine-character*), nhận bone phụ: tóc, áo choàng, váy, đuôi, dây, tai — theo tên part (Claude đã đặt tên)
  + hình dạng (dài, treo dưới bone cha). Chia chuỗi 2–4 bone nếu part dài (weighted mesh đã có ở PLAN-spine-weighted-mesh).
- Thêm `physics` constraint Spine 4.2 (x/rotate, inertia, strength, damping, mass, wind, gravity) theo preset: tóc nhẹ, vải nặng, đuôi.
  Kiểm định dạng JSON đúng spec Spine 4.2 (`physics` mảng top-level, constraint có `bone`, `x`/`rotate`…).
- Xuất bản dùng được spine-unity / spine-godot ≥ 4.2; ghi rõ yêu cầu runtime trong README export.
- Nghiệm thu: test JSON schema; nếu project test có runtime Spine 4.2 thì chạy walk thấy lắc, không thì ghi rõ chỉ kiểm schema.

### #52 audit project game (B)
- API `POST /api/audit` {path, engine?: auto} → báo cáo; `POST /api/audit/fix` {path, actions[], dry_run} có backup zip + hoàn tác
  (dùng lại cơ chế an toàn của `export/restyle.py`: chặn path ra ngoài, symlink, file đổi sau quét).
- Quét: texture (kích thước file vs hiển thị nếu đo được, max size/nén hiện tại trong .meta/.import, không POT, alpha thừa),
  ảnh trùng (pHash; CLIP nếu có), asset không ai tham chiếu (Unity GUID trong .unity/.prefab/.asset/.mat; Godot `res://` + uid trong
  .tscn/.tres/.gd), mesh tris > ngân sách mobile, audio import setting (chỉ báo, không sửa).
- Sửa 1 chạm: áp preset mobile #31 vào .meta/.import, giảm max size, gộp trùng (chỉ báo, không tự xoá), tạo atlas (#30) tuỳ chọn.
- UI: màn `/audit` (chọn thư mục, bảng nhóm theo loại, MB trước/sau, nút sửa, hoàn tác) + mục sidebar. MCP tool `audit_project`,
  `audit_fix` (thêm vào mcp/server.py, chỉ thêm).
- Nghiệm thu: 3 project test (bản copy tạm!), sửa xong Unity/Godot mở 0 lỗi, 0 missing ref, ước tính MB giảm.

### #54 minimap (B)
- Input ảnh màn (mockup / screenshot) hoặc Tiled `.tmx`/LDtk JSON + danh sách POI (tên, toạ độ, loại) tuỳ chọn.
- Kiểu: `clean` (CPU: phân vùng màu, đơn giản hoá, viền), `parchment` / `stylized` (Klein restyle). Icon POI từ icon-pack hoặc vẽ CPU.
- Xuất ảnh + JSON POI (toạ độ chuẩn hoá 0–1 + pixel), Unity prefab RawImage + Mask tròn, Godot scene TextureRect + shader mask tròn.
- Nghiệm thu: POI đặt đúng ±2px, prefab/scene mở được.

### #48 vfx-particles (C)
- Texture pack 64–256px nền trong: soft dot, spark, smoke puff (4 biến thể), ring, slash arc, star, magic rune — procedural CPU
  là chính (sạch, nhẹ), Z-Image/Klein cho texture "có style" theo prompt (lửa, phép).
- Preset emitter JSON trung lập (rate, burst, lifetime, speed, gravity, size/color over life, shape): `explosion`, `heal`, `pickup`,
  `projectile_trail`, `dust_step`, `hit_spark`, `magic_aura`.
- Xuất: Unity prefab `ParticleSystem` (+ `TrailRenderer` cho đạn; material Particles/Unlit URP và Built-in), Godot `GPUParticles2D`
  + `ParticleProcessMaterial` .tres (và 3D tuỳ chọn). Sheet nhiều ô → Texture Sheet Animation / `particles_anim_*`.
- Nối #38: tuỳ chọn hit-FX dùng preset `hit_spark`.
- Nghiệm thu: Unity batchmode load prefab, Simulate 1s, particle count > 0; Godot headless chạy 30 frame, chụp ảnh không trống.

### #45 trim-sheet + decal-pack (D)
- `trim-sheet`: Claude chọn bố cục dải (cao 32/64/128/256 trên 2048, tổng = 2048), Z-Image sinh từng dải seamless ngang
  (dùng lại texture-seamless/op tile 1 trục), ghép atlas → pbr-material ra normal/rough/height/AO. JSON dải {name, v0, v1, px}.
- `decal-pack`: nứt, rêu, bẩn, vết cháy, sơn graffiti/logo — nền trong (BiRefNet hoặc alpha từ luminance), normal map; xuất Unity
  URP Decal Projector prefab (+ material `Shader Graphs/Decal`), Godot `Decal` node .tscn.
- Blender helper: script map UV 1 mặt vào dải theo tên (dùng cho #46).
- Nghiệm thu: seam ngang mỗi dải (score như #43), cube bevel trong Blender UV đúng dải, Unity/Godot mở material không lỗi.

### #46 modular-kit (D)
- Bộ mảnh theo lưới (mặc định 2m, chọn 1/2/4): wall, wall_window, wall_door, corner_in/out, floor, ceiling, stair, pillar, railing;
  style từ prompt (gạch, gỗ, sci-fi). Blender procedural (bmesh, bevel), pivot góc dưới-trái, kích thước đúng bội lưới.
- Vật liệu: trim sheet #45 (UV map theo dải) hoặc texture-seamless; tuỳ chọn #41 retexture sau.
- Xuất GLB/FBX từng mảnh, Unity prefab + collider, Godot `MeshLibrary` .tres cho GridMap + scene phòng mẫu; ảnh render phòng mẫu ghép.
- Nghiệm thu: bounds = bội lưới ±0.5mm, 2 tường kề không hở (đỉnh biên trùng), tris/mảnh ≤ 2k mặc định.

### #47 terrain-3d (E)
- Prompt → Claude ra bản đồ vùng (JSON: vùng núi/đồi/đồng/nước/sông, toạ độ thô 0–1, độ cao tương đối) → CPU heightmap 16-bit
  1025/2049 (fBm + domain warp + hydraulic erosion nhẹ, sông khắc theo spline) → splat 4 kênh theo độ cao/độ dốc/vùng.
- 4 lớp texture: texture-seamless (Z-Image) + pbr-material; tuỳ chọn ghép skybox #43 cùng prompt.
- Unity: editor script tạo `TerrainData` .asset + 4 `TerrainLayer` + scene mẫu; Godot: mesh chia chunk (LOD đơn giản) + shader splat 4 lớp
  + collision HeightMapShape3D.
- Xuất thêm `.raw` 16-bit + PNG16 cho engine khác.
- Nghiệm thu: không bậc thang (histogram 16-bit liên tục), splat tổng = 1, preview Unity/Godot chụp được, Godot không xuyên sàn.

### #50 ui-screen (F)
- Input: loại màn (home, shop, level_select, pause, result_win, result_lose, settings) + project style/ui-kit đã duyệt (hoặc chạy ui-kit trước).
- Claude ra layout JSON (cây node: panel/button/label/icon/list, anchor, padding, 9-slice, font, safe area) → render mockup PNG CPU
  từ mảnh ui-kit + bitmap-font → gate duyệt.
- Xuất Unity UGUI prefab (Canvas Scaler 1080×1920 match 0.5, Image sliced, TMP text nếu có font TMP else legacy Text),
  UI Toolkit UXML/USS tuỳ chọn; Godot Control `.tscn` + Theme `.tres` (StyleBoxTexture 9-slice).
- Nghiệm thu: prefab/scene mở 0 missing; render ở 16:9 / 20:9 / 4:3 không tràn safe area (chụp engine preview 3 tỉ lệ).

### #53 plugin editor Unity/Godot (G)
- Client gọi HTTP API core local (đọc lock file như `mcp/link.py` để lấy port + token; không có core → nút "Mở AI Asset Studio").
- Unity package `com.assetstudio.bridge` (Editor-only asmdef): EditorWindow (chọn recipe, form input sinh từ describe_recipe,
  project, tiến trình, duyệt gate ảnh, nhận output vào `Assets/AssetStudio/<recipe>/` qua exporter Unity có sẵn), menu chuột phải asset
  → Restyle / Tô lại texture / Biến thể kẻ địch / Thêm vào Dự án.
- Godot addon `addons/asset_studio/` (EditorPlugin dock, GDScript, HTTPRequest) tương tự, output vào `res://asset_studio/`.
- App: nút "Cài plugin vào project…" (copy package/addon vào project được chọn) ở màn Cài đặt hoặc Engine.
- Nghiệm thu: Unity batchmode compile 0 lỗi + test EditMode gọi API thật (core dev), Godot headless load plugin 0 lỗi; 1 run live
  item-spin hoặc concept-2d từ Unity → asset vào Assets.

### #55 Agent điều khiển studio: MCP + skill + plugin (H)
Mục tiêu: Claude Code / Codex CLI (và agent MCP khác) tự làm mọi việc chủ nhân làm trên UI.
- **MCP mở rộng** (`mcp/server.py`, đã có 21 tool): thêm tool còn thiếu so với UI — xem báo cáo dò ở cuối file
  (tạo/sửa project + khoá style, import file vào Thư viện, "dùng tiếp cho" (follow-up) giữa recipe, `generate` một-phát
  (start + wait + auto_first + outputs, dành cho agent đơn giản), trạng thái hệ thống (core, ComfyUI, Blender, GPU lock, model thiếu),
  restyle/audit đi qua tool riêng nếu cần, xuất thẳng vào project Unity/Godot theo đường dẫn). Resource MCP: catalog recipe dạng markdown.
- **Skill** `asset-studio` (SKILL.md + references/recipes.md sinh tự động từ registry): chọn recipe theo nhu cầu, luồng mẫu
  (nhân vật → 8 hướng → Spine → pack; cảnh 3D: terrain + skybox + kit), gate policy, thời gian/GPU, cách xử lý lỗi/timeout.
  Script `scripts/gen_agent_docs.py` sinh lại references từ recipe yaml (test so khớp để không lệch).
- **Plugin Claude Code** `integrations/claude-plugin/` (`.claude-plugin/plugin.json` + `marketplace.json`, `.mcp.json` trỏ
  `studio-core.exe mcp` bản cài / `uv run` bản dev, `skills/asset-studio/`, vài slash command: `/asset-studio:make`, `:status`, `:pack`).
- **Codex CLI**: `integrations/codex/` — đoạn `config.toml` `[mcp_servers.asset-studio]` + skill cùng nội dung (Codex skills
  `~/.codex/skills/asset-studio/SKILL.md`) + AGENTS.md mẫu.
- **CLI không cần MCP** (cho script/agent khác): `studio-core agent run <recipe> --input k=v --wait --gate auto_first --json`,
  `agent recipes`, `agent outputs <run>` — mỏng, gọi qua CoreLink.
- **App**: Cài đặt → "Kết nối agent": nút cài cho Claude Code (chạy `claude mcp add --scope user …` hoặc cài plugin marketplace local)
  và Codex (`codex mcp add …` hoặc ghi config.toml có backup), trạng thái đã cài/chưa, copy lệnh tay. Không tự chạy nếu CLI không có.
- Nghiệm thu: test MCP tool mới (fake core như test MCP hiện có); chạy thật `claude -p` hoặc `codex exec` không tương tác gọi
  `list_recipes` + 1 run nhỏ CPU (ví dụ ui-juice/item-spin 2D) qua MCP → có output; ghi transcript ngắn.

### R — review + build + e2e (sau khi A–H xong)
- Review toàn bộ diff (đúng spec, an toàn path, không rò rỉ token, comment theo luật), sửa lỗi tìm thấy.
- pytest toàn bộ, typecheck, `cd app && npm run dist:core && npm run dist:portable`.
- E2E bằng core đóng gói (data riêng trong scratchpad, như e2e110): mọi recipe mới succeeded + engine preview; MCP qua `studio-core.exe mcp`.
- Báo cáo; không commit.

## Bổ sung (chủ nhân, sau khi soạn)
- Blender (có MCP `blender`) và Unity (có MCP `unity-editor-mcp` + CLI) được **bật lên dùng bất cứ lúc nào**: agent được mở Blender GUI /
  Unity Editor để kiểm tra trực quan (`mcp__blender__look`, `get_scene_info`, Unity MCP), không chỉ batchmode. Blender/Unity GUI
  đang mở do agent khác → không đóng; Unity batchmode không chạy được trên project đang mở Editor → dùng project test khác hoặc MCP.
  Live GPU Blender vẫn theo `.gpu-lock`.

## Kết quả dò hiện trạng MCP (cho #55)
- Server `core/studio_core/mcp/server.py` (stdio, `studio-core(.exe) mcp [--data-dir]`), 25 tool: list_recipes, describe_recipe,
  start_run, list_projects, get_run, wait_run, resolve_gate, cancel_run, retry_step, get_outputs, list_runs, list_versions,
  set_version_status, get_asset_list, asset_list_from_gdd, run_asset_list, project_qa, project_qa_fix, find_similar_assets,
  find_duplicate_assets, get_style_bible, build_style_bible, build_asset_pack, recipe_stats, engine_preview.
  `mcp/link.py` CoreLink: gắn core đang chạy qua lock file hoặc tự spawn. Test: `tests/test_p4b_mcp.py`, `test_engine_preview.py`.
- App đã có `components/AgentConnectCard.vue` (sinh lệnh `claude mcp add -s user asset-studio -- …` + TOML Codex) qua IPC
  `studio:mcpInfo` (`app/src/main/index.ts:306`). Chưa có: plugin, skill, cài 1 chạm, CLI agent, tool còn thiếu.
- Recipe: yaml trong `core/studio_core/recipes/` tự nạp; op = `OpExecutor` `kind="op.x"` → tuple `*_OPS` → `ops/__init__.py`;
  catalog kind ở `engine/kinds.py`; art `app/src/renderer/src/assets/art/index.ts` + `app/scripts/gen-art.py`/`art-manifest.json`.
- #55 thêm: skill nhắc luồng kết hợp MCP blender/unity (vd get_outputs → import vào Unity qua Unity MCP, xem mesh bằng Blender look).
- (chủ nhân, bổ sung #55) Khi agent điều khiển qua MCP mà app đang mở → app hiện flow + trạng thái trực tiếp: agent nào đang kết nối,
  tool đang gọi, run/bước đang chạy, đang đợi gì (Unity import/dựng model, Blender render, ComfyUI queue, GPU lock, gate). MCP gửi
  sự kiện hoạt động về core → WS → panel "Agent đang điều khiển" + flow view sáng node đang chạy + substatus của step. Giao Agent H.

## Hiệu năng (chủ nhân chốt 2026-10-29, làm SAU khi A–H xong, trước R)
Đo thực (recipe_stats all-time, 27 run): GPU ~60%, Claude ~38%, CPU op ~1.5% công việc; wall trung bình 1016s vs active 97s.
→ Không viết lại core Rust. Làm:
- **P2 chạy chồng lane**: khi GPU bận, cho bước lane cpu/blender/claude của run khác chạy song song (kiểm scheduler/lane trong engine/runner;
  giữ giới hạn 1 GPU, Blender theo số instance an toàn). Nghiệm thu: 2 run đồng thời (1 GPU + 1 CPU/Claude) tổng wall giảm; test scheduler.
- **P3 cache**: tăng cache hit (hiện 6.25%): khoá cache theo (kind, input hash, param chuẩn hoá, model/version) cho bước deterministic + claude.*
  khi input y hệt; tuỳ chọn "bỏ qua cache" khi chạy lại; không cache bước có seed ngẫu nhiên trừ khi seed cố định. Nghiệm thu: chạy lại run
  giống hệt → hit, wall giảm; test đảm bảo không trả cache sai khi đổi param/model.
