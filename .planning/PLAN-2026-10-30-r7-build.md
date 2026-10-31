# PLAN — làm lượt 7 (#56–#58 + dư lượt 4 + nợ lượt 6) — 2026-10-30

Chủ nhân: "ok làm hết cho anh, chia subagent làm rồi em quản lý nhé".
Ý tưởng: `.planning/PLAN-2026-10-30-feature-ideas-r7.md` (#56–#58), `PLAN-2026-10-30-feature-ideas.md` (#34–#39),
nợ: `HANDOFF-2026-10-30-0252.md` §3.4.

## Luật chung cho mọi agent
- Cùng working tree `L:\AI\AI-Asset-Studio` (không worktree). Mỗi agent chỉ sửa file thuộc phần mình.
- File dùng chung (`engine/kinds.py`, `ops/__init__.py`, `api/routes.py`, `mcp/server.py`, `export/{unity,godot,source}.py`,
  `store/bundle.py`, `packaging/studio-core.spec`, `store/model_licenses.py`, app `art/index.ts`, `art-manifest.json`, `followUp.ts`,
  `types.ts`, `router.ts`, `AppSidebar.vue`): **chỉ thêm**, đọc lại ngay trước khi Edit, không format lại file.
  `app/src/renderer/src/composables/changelog.ts`, `app/package.json` version và `.planning/*` để con chính.
- Recipe mới → chạy `uv run python scripts/gen_agent_docs.py` (skill catalog) trước khi báo xong; `--check` phải OK.
- GPU: `mkdir L:/AI/AI-Asset-Studio/.gpu-lock` (atomic) trước mọi live run ComfyUI/Blender GPU/Wan; đang có khoá → poll 30s;
  xong xoá khoá **mình** tạo. Không giết ComfyUI/Blender/Unity của agent khác. Live run giữ ngắn (1 lượt đại diện).
  ComfyUI: `L:\AI\setup\run-comfyui.ps1` (127.0.0.1:8188, `--disable-pinned-memory`), model ở `F:\work\AI\comfyUI_models`.
- Không git commit/push. Không `npm run build/dist` (chỉ R). Được chạy `cd core && uv run python -m pytest -q tests`
  (hoặc test của mình), `cd app && npm run typecheck`.
- Comment ít: chỉ "tại sao" (workaround, ràng buộc engine/SDK, magic number có nguồn). Không header trang trí, không đánh số bước.
- Unity: CLI `L:\setup\unity\bin\unity.exe` (6000.3.27f1), project test `L:\AI\unity-test-batch` (Built-in), `L:\AI\unity-urp-shadercheck` (URP);
  MCP `unity-editor-mcp`, skill `unity:unity-cli`. Godot 4.7 tự dò (`config.find_godot`), `--import` headless đặt timeout.
  Blender: MCP `blender` + headless. Blender/Unity GUI được mở để kiểm trực quan; GUI do agent khác mở thì không đóng.
- **An toàn project test**: preview Unity phải truyền export_dir có tên (guard có sẵn). Thao tác có thể xoá/ghi đè thì làm trên **bản copy**
  trong scratchpad. Không đụng `L:\AI\AI Asset Studio Portable\data`. Không sửa config Claude/Codex thật của chủ nhân (`~/.claude.json`,
  `~/.codex/config.toml`) — test dùng HOME/USERPROFILE tạm.
- Không audio, không cloud, không train LoRA. Model mới chỉ F (#39 SeedVR2).
- Chức năng xong khi: recipe yaml + op + exporter Unity/Godot + UI (thẻ recipe + art thẻ, FollowUpMenu nếu hợp) + test (fake executor
  cho Comfy/Blender) + pytest toàn bộ pass + typecheck sạch + 1 live run thật (hoặc ghi rõ vì sao không) + mở được trong Unity/Godot.
  File mới mà `packaging/studio-core.spec` liệt kê thủ công → thêm vào spec.
- Báo cáo cuối (≤40 dòng): file thêm/sửa, số test, live (thời gian, VRAM, số liệu nghiệm thu), nợ còn lại, 1 dòng changelog tiếng Việt.

## Phân công
| Agent | Phần | Recipe gợi ý | GPU |
|---|---|---|---|
| A | #57 icon từ 3D + #36 isometric | `icon-3d`, `iso-tiles`, `iso-prop` | Blender, Z-Image |
| B | #56 tile động | `tile-animated` | Wan2.2 (lửa/thác) |
| C | #58 màn chơi thử (gộp #35) | `playable-scene` | không (Claude + engine) |
| D | #34 biểu cảm Spine + nợ Spine physics | `spine-face` (hoặc bước trong spine-character) | Klein |
| E | #37 thẻ bài | `card-set`, `card-set-prompt` | Klein/Z-Image |
| F | #39 SeedVR2 upscale | tham số `engine: seedvr2` trong `upscale` | SeedVR2 (model mới) |
| G | Nợ 3D/VFX: decal, trim, modular hồng, terrain nước, VFX | — | Z-Image, Blender |
| H | Nợ app/agent: UI screen TMP, minimap FollowUp, audit CLIP, version core, Codex thật, GUI plugin | — | ít |
| R | Sau cùng: review + fix, pytest, typecheck, dist, e2e core đóng gói | — | các recipe mới |

## Spec

### #57 icon-3d (A)
- Vào: nhiều mesh (GLB/FBX/OBJ; cho phép thư mục/list) + preset góc (`iso34` 3/4 trên 30°, `front`, `top`), size 128/256/512, padding,
  outline (px, màu), toon on/off, nền: trong | độ hiếm (#14 `item-rarity` bậc 1–5) | khung icon-pack.
- Blender headless: ortho camera fit bounding box (cùng tỉ lệ lấp đầy cho cả bộ), đèn 3 điểm cố định, màu/exposure cố định → đồng bộ.
- Ra: PNG từng icon (tên = tên mesh) + atlas (dùng #30) + Unity Sprite / Godot texture qua exporter có sẵn. FollowUp từ prop-3d/library mesh.
- Nghiệm thu: tâm khối ±2px, độ lấp đầy chênh ≤5% giữa icon, alpha sạch (không viền đen).

### #36 isometric 2:1 (A)
- `iso-tiles`: prompt/ảnh → tile mặt đất iso 2:1 (64×32 / 128×64 / 256×128) liền mép theo hình thoi, biến thể 4–8; Z-Image (ControlNet nếu cần)
  hoặc texture-seamless rồi chiếu lên hình thoi.
- `iso-prop`: GLB (hoặc ảnh→TRELLIS có sẵn) → Blender camera iso đúng góc (60°/45°, ortho), 4 hướng quay 90°, pivot chân, nền trong.
- Xuất: Godot TileSet `tile_shape=ISOMETRIC` + y-sort; Unity Tilemap Isometric (Grid cell layout Isometric, cell size 1×0.5) + sort axis Y.
- Nghiệm thu: ghép lưới 4×4 không hở mép; prop đứng đúng ô.

### #56 tile-animated (B)
- Vào: 1 tile (hoặc tile từ tileset-seamless/autotile) + kiểu: `water`, `lava`, `grass_wind`, `torch`, `waterfall`, `custom`(prompt) + số frame 4–16 + fps.
- water/lava/grass: CPU flow/displacement có chu kỳ (noise tuần hoàn theo thời gian, wrap mép) → liền mép tuyệt đối, lặp đúng chu kỳ.
- torch/waterfall/custom: Wan2.2 TI2V trên ảnh lát 3×3, cắt ô giữa, chọn đoạn lặp như #40 (`sprite-anim-video`), sau đó seam blend.
- Ra: sheet + Godot TileSetAtlasSource `animation_frames_count/animation_speed` + Unity AnimatedTile (nếu project có 2D Tilemap Extras; không thì
  Sprite anim + ghi README) ; blob-47 động nếu đầu vào là autotile (áp cùng field chuyển động cho cả bộ).
- Nghiệm thu: sai lệch mép trái/phải, trên/dưới và frame cuối→đầu ≤ ngưỡng texture-seamless.

### #58 playable-scene (C) — gộp #35
- Vào: project_id hoặc danh sách version (nhân vật 8 hướng/Spine, tileset/autotile, prop, parallax/skybox tuỳ chọn) + engine godot|unity + kiểu
  `topdown` | `sideview`.
- Claude bố cục màn nhỏ (JSON lưới ~32×20: nền, tường/đường, vị trí prop, spawn) — tái dùng logic minimap/ui-screen nếu có; fallback procedural.
- Godot: project tạm → TileMapLayer + CharacterBody2D (8 hướng, AnimatedSprite2D từ sheet), Camera2D, prop có collider (#13), Parallax2D.
  Unity: scene Tilemap + PlayerController đơn giản + Animator từ clip có sẵn.
- Xuất thêm Tiled `.tmx` + LDtk `.ldtk` (#35) và ảnh mockup toàn màn.
- Chạy headless vài giây (Godot `--headless` + script chụp; Unity batch render) → ảnh preview vào app; nút "Mở trong Godot/Unity".
- Nghiệm thu: scene mở 0 lỗi, nhân vật di chuyển được (test script mô phỏng input vài frame, vị trí đổi), mockup khớp lưới.

### #34 biểu cảm mặt Spine (D)
- Sau spine-character*: tìm slot đầu/mặt → Klein inpaint vùng mắt/miệng: mắt mở/nhắm/nửa, miệng A/O/M/cười, cau mày; attachment riêng trong slot
  `eyes`/`mouth`/`brow` (tách khỏi head nếu cần, giữ đúng vị trí).
- Anim: `blink` (idle tự chớp 3–5s, ngẫu nhiên có seed), `talk` (A/O/M luân phiên), skin hoặc anim cảm xúc `happy`/`angry`/`sad`.
- Nghiệm thu: attachment khớp ±1px, JSON hợp lệ Spine 4.2, xem trong spine-player của app.
- Nợ: kiểm Spine physics (#51) trong Unity/Godot thật: tải spine-unity 4.2 (UPM git) vào **bản copy** project test hoặc spine-godot 4.2 nếu có
  bản build; không có thì ghi rõ. Tăng độ lắc preset vải.

### #37 thẻ bài (E)
- Vào: danh sách thẻ (CSV/JSON: tên, mô tả, cost, atk/hp, độ hiếm, loại) hoặc prompt → Claude sinh danh sách (`card-set-prompt`).
- Art: Klein/Z-Image theo style bible dự án; khung theo độ hiếm (#14) + loại; chữ dùng bitmap font (#25) hoặc TTF có dấu tiếng Việt; tự co chữ vừa ô.
- Ra: PNG từng thẻ (mặt trước + mặt sau chung), atlas, JSON dữ liệu; Unity prefab UGUI card (Image + Text/TMP nếu có) / Godot scene Control + Resource.
- Nghiệm thu: chữ không tràn, đúng dấu tiếng Việt, kích thước thẻ chuẩn (63×88 tỉ lệ), 1 lô 6 thẻ live.

### #39 SeedVR2 upscale (F)
- Spike: custom node ComfyUI SeedVR2 (numz/ComfyUI-SeedVR2_VideoUpscaler) + model 3B (fp8/GGUF nếu có) vào `F:\work\AI\comfyUI_models`;
  kiểm license (Apache-2.0) → `store/model_licenses.py`. Đo VRAM/thời gian 512→2048 trên 12GB (tile/blockswap nếu cần).
- Tích hợp: `upscale.yaml` thêm lựa chọn engine `esrgan|seedvr2` (mặc định esrgan nếu chưa cài), giữ alpha (upscale RGB + alpha riêng), tắt cho
  pixel art (giữ nearest). Gói model trong trình cài 1 chạm (manifest model) dạng tuỳ chọn.
- Nếu không chạy được ổn trên 12GB → báo cáo số liệu, để engine ẩn, không phá upscale hiện có.

### G — nợ 3D/VFX
- Decal dirt/scorch không ra vòng tròn (chạy lại prompt đã sửa, thêm mask nhiễu/biên bất định); trim ép màu theo vật liệu (gỗ ra màu gỗ).
- modular-kit preview Unity hồng: chọn shader theo render pipeline project (Built-in Standard / URP Lit), áp cho mọi exporter 3D dùng chung.
- terrain: mesh nước cho hồ/biển/sông (Unity plane + material nước đơn giản, Godot MeshInstance3D + shader), hồ miệng núi lửa đúng mực nước;
  2 terrain trong 1 project Unity không trùng script (namespace/tên theo export).
- VFX: Godot burst delay đúng, 3D trail/projectile có stretch (Unity Stretched Billboard, Godot particle flag align/scale theo vận tốc).

### H — nợ app/agent
- ui-screen: dùng TMP nếu project có TextMeshPro (fallback Text), icon thật từ icon-pack thay icon tạm.
- minimap FollowUp chỉ hiện cho ảnh kiểu màn chơi/tiled (không mọi PNG).
- audit: ảnh trùng dùng CLIP nếu model có (đã có ở find_similar), fallback pHash.
- core `pyproject.toml` + `system_status` báo 1.2.0 (đồng bộ app version; lấy từ một nguồn).
- Dọn backup `~/.claude.json` cũ (giữ N bản mới nhất) trong agentInstall.
- Codex thật: `codex exec` với HOME tạm + config sinh từ integrations/codex, gọi MCP list_recipes + 1 run nhẹ (ui-juice). Sửa lỗi gặp phải.
- Plugin Unity/Godot: mở GUI (Unity MCP screenshot / Godot editor) trên bản copy project, chụp, sửa lỗi hiển thị.

### R — review + build + e2e (sau khi A–H xong)
- Review toàn bộ diff (đúng spec, an toàn path, không rò token, comment theo luật), sửa lỗi.
- pytest toàn bộ, typecheck, `cd app && npm run dist:core && npm run dist:portable` (release dir bị khoá → đổi output dir như lượt 6).
- E2E core đóng gói (data riêng trong scratchpad): mọi recipe mới succeeded + engine preview; MCP qua `studio-core.exe mcp`.
- Báo cáo; không commit.
