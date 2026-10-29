import appIcon from './app-icon.webp'
import emptyRuns from './empty-runs.webp'
import emptyGates from './empty-gates.webp'
import emptyGallery from './empty-gallery.webp'
import coreDisconnected from './core-disconnected.webp'
import gettingStarted from './getting-started.webp'
import concept2d from './recipe-concept-2d.webp'
import concept2dCodex from './recipe-concept-2d-codex.webp'
import concept2dBrief from './recipe-concept-2d-brief.webp'
import variants2d from './recipe-variants-2d.webp'
import sprite2d from './recipe-sprite-8dir-2d.webp'
import sprite2dPrompt from './recipe-sprite-8dir-2d-prompt.webp'
import sprite3d from './recipe-sprite-8dir-3d.webp'
import sprite3dBrief from './recipe-sprite-8dir-3d-brief.webp'
import sprite3dMv from './recipe-sprite-8dir-3d-mv.webp'
import spriteGlb from './recipe-sprite-8dir-glb.webp'
import prop3d from './recipe-prop-3d.webp'
import procedural3d from './recipe-procedural-3d.webp'
import spineCharacter from './recipe-spine-character.webp'
import spineCharacterMv from './recipe-spine-character-mv.webp'
import spriteWalk from './recipe-sprite-8dir-walk.webp'
import textureSeamless from './recipe-texture-seamless.webp'
import tilesetSeamless from './recipe-tileset-seamless.webp'
import concept2dStyle from './recipe-concept-2d-style.webp'
import spriteAnim from './recipe-sprite-8dir-anim.webp'
import spritePixel from './recipe-sprite-8dir-pixel.webp'
import sprite3dPixel from './recipe-sprite-8dir-3d-pixel.webp'
import spineSkinVariant from './recipe-spine-skin-variant.webp'
import spineAnimRef from './recipe-spine-anim-ref.webp'
import audioSfx from './recipe-audio-sfx.webp'
import audioMusic from './recipe-audio-music.webp'
import vfxFlipbook from './recipe-vfx-flipbook.webp'
import upscale from './recipe-upscale.webp'
import uiKit from './recipe-ui-kit.webp'
import uiKitStyle from './recipe-ui-kit-style.webp'
import iconPack from './recipe-icon-pack.webp'
import spineCharacterImage from './recipe-spine-character-image.webp'

export const ART = { appIcon, emptyRuns, emptyGates, emptyGallery, coreDisconnected, gettingStarted } as const

const RECIPE_ART: Record<string, string> = {
  'concept-2d': concept2d,
  'concept-2d-codex': concept2dCodex,
  'concept-2d-brief': concept2dBrief,
  'concept-2d-style': concept2dStyle,
  'variants-2d': variants2d,
  'sprite-8dir-2d': sprite2d,
  'sprite-8dir-2d-prompt': sprite2dPrompt,
  'sprite-8dir-3d': sprite3d,
  'sprite-8dir': sprite3d,
  'sprite-8dir-3d-brief': sprite3dBrief,
  'sprite-8dir-3d-mv': sprite3dMv,
  'sprite-8dir-glb': spriteGlb,
  'sprite-8dir-walk': spriteWalk,
  'sprite-8dir-anim': spriteAnim,
  'sprite-8dir-paperdoll': spriteAnim,
  'character-3d-rigged': spriteGlb,
  'character-3d-rigged-image': spriteAnim,
  'sprite-8dir-pixel': spritePixel,
  'sprite-8dir-3d-pixel': sprite3dPixel,
  'prop-3d': prop3d,
  'procedural-3d': procedural3d,
  'spine-character': spineCharacter,
  'spine-character-image': spineCharacterImage,
  'spine-character-mv': spineCharacterMv,
  'spine-skin-variant': spineSkinVariant,
  'spine-anim-ref': spineAnimRef,
  'texture-seamless': textureSeamless,
  'tileset-seamless': tilesetSeamless,
  'audio-sfx': audioSfx,
  'audio-music': audioMusic,
  upscale,
  'ui-kit': uiKit,
  'ui-kit-style': uiKitStyle,
  'icon-pack': iconPack,
  'vfx-flipbook': vfxFlipbook
}

export function recipeArt(id: string): string | undefined {
  return RECIPE_ART[id]
}

// What a card promises, in the user's words; the step list stays in the tooltip-level detail.
const RECIPE_PITCH: Record<string, string> = {
  'concept-2d': 'Prompt → ảnh PNG nền trong suốt',
  'concept-2d-codex': 'Prompt → ảnh PNG nền trong suốt, không cần GPU',
  'concept-2d-brief': 'Ý tưởng ngắn → Claude viết prompt → ảnh PNG nền trong suốt',
  'variants-2d': 'Ảnh có sẵn + lệnh sửa → nhiều biến thể + sprite sheet',
  'sprite-8dir-2d': 'Ảnh mặt trước → sheet 8 hướng nhanh, góc gần đúng (xem thử)',
  'sprite-8dir-2d-prompt': 'Prompt → sheet 8 hướng nhanh, góc gần đúng (xem thử)',
  'sprite-8dir-3d': 'Prompt → sprite sheet 8 hướng đúng góc, dựng qua mesh 3D',
  'sprite-8dir-3d-mv': 'Prompt → sprite sheet 8 hướng, mặt sau chuẩn nhất',
  'sprite-8dir-3d-brief': 'Ý tưởng ngắn → Claude viết brief → sprite sheet 8 hướng',
  'sprite-8dir-glb': 'Mesh GLB có sẵn → sprite sheet 8 hướng',
  'prop-3d': 'Prompt → vật phẩm 3D (GLB có texture)',
  'procedural-3d': 'Ý tưởng → Claude dựng 3D low-poly bằng code → GLB',
  'sprite-8dir-walk': 'Prompt → sprite 8 hướng + sheet đi bộ 8 hướng, dựng qua mesh 3D',
  'sprite-8dir-anim': 'Prompt → sprite 8 hướng + bộ anim đứng / đi / chạy / chém, kèm normal map',
  'sprite-8dir-paperdoll': 'Nhân vật anim 8 hướng có sẵn → vũ khí / khiên / mũ / giáp thành lớp riêng khớp từng khung + sockets.json',
  'character-3d-rigged': 'Mesh nhân vật có sẵn → rig Humanoid + anim đứng / đi / chạy / chém, xuất GLB + FBX',
  'character-3d-rigged-image': 'Ảnh nhân vật mặt trước → mesh 3D có rig Humanoid + anim, xuất GLB + FBX',
  'sprite-8dir-pixel': 'Mesh GLB có sẵn → sprite sheet 8 hướng pixel art, một bảng màu chung',
  'sprite-8dir-3d-pixel': 'Prompt → sprite sheet 8 hướng pixel art, dựng qua mesh 3D',
  'spine-character': 'Prompt → nhân vật Spine 4.2 có xương + 6 anim',
  'spine-character-mv': 'Prompt → 2 bộ Spine mặt trước + mặt sau, cùng 6 anim',
  'spine-character-image': 'Ảnh nhân vật mặt trước → Spine 4.2 có xương + 6 anim',
  'texture-seamless': 'Prompt vật liệu → texture lặp liền mạch + điểm mối nối, normal map, bản nhỏ',
  'tileset-seamless': 'Prompt vật liệu → nhiều biến thể chung mép, lát trộn lẫn không lộ nối',
  'audio-sfx': 'Mỗi dòng 1 hiệu ứng → vài bản âm thanh OGG + WAV, đã cắt lặng và chuẩn hoá',
  'audio-music': 'Prompt + BPM → nhạc nền, có thể lặp vòng tròn ô nhịp, OGG + WAV',
  'scene-soundscape': 'Ảnh cảnh → Claude soạn nhạc nền + bản căng thẳng cùng BPM, ambience lặp, 3–5 SFX môi trường, xuất Unity/Godot',
  'npc-pack': 'Ảnh chân dung NPC → 8 biểu cảm cùng khung + thoại đúng giọng (vi/en) + giọng đọc + phụ đề, xuất NpcData Unity/Godot',
  'npc-pack-prompt': 'Prompt → chân dung NPC → 8 biểu cảm + thoại đúng giọng (vi/en) + giọng đọc + phụ đề, xuất Unity/Godot',
  'npc-voice-csv': 'CSV thoại (id,lang,text,expression) → giọng đọc OGG + phụ đề có mốc thời gian, không cần GPU',
  'vfx-flipbook': 'Prompt → hiệu ứng VFX nền trong suốt → chuỗi frame / flipbook',
  'concept-2d-style': 'Ảnh phong cách tham chiếu + prompt → concept 2D cùng phong cách',
  'spine-skin-variant': 'Nhân vật Spine + ảnh sửa → skin mới, tô lại atlas giữ nguyên xương',
  'spine-anim-ref': 'Nhân vật Spine + video tham chiếu → anim mới cho bộ xương sẵn có',
  'ui-kit': 'Chủ đề → bộ nút, khung, thanh, ô có viền 9-slice + trạng thái nút, xuất Unity/Godot',
  'ui-kit-style': 'Ảnh style tham chiếu + chủ đề → bộ UI 9-slice cùng phong cách',
  'icon-pack': 'Mỗi dòng 1 icon → cả bộ icon cùng khung, nét viền, đổ bóng',
  upscale: 'Ảnh / atlas có sẵn → bản x2, x4 nét hơn, nền trong suốt vẫn sạch',
  'pbr-material': 'Texture có sẵn → bộ map PBR (normal, height, roughness, metallic, AO) + material Unity/Godot, không cần GPU',
  'sprite-normal-map': 'Sprite / sheet → normal map 2D (viền vát tròn theo alpha, từng khung) để nhận Light2D, chạy CPU',
  'item-rarity': 'Ảnh vật phẩm → 5 bậc common → legendary: đổi màu giữ nét, Klein thêm viền vàng / đá quý, glow + shimmer',
  'damage-states': 'Ảnh thùng / cửa / tường → nứt, vỡ, nát cùng khung + 4–8 mảnh vỡ, chớp trắng, prefab Unity / scene Godot',
  'time-season-pack': 'Tileset / background / prop → bản hoàng hôn, đêm, đông, thu giữ nguyên bố cục + LUT .cube / strip cho Unity, Godot',
  'store-kit': 'Tên game + mô tả → icon app đủ cỡ iOS / Android adaptive / Play / favicon / .ico + capsule, hero, logo Steam có tên game, zip sẵn nộp store',
  'store-kit-image': 'Ảnh nhân vật + tên game → icon app + key art giữ đúng nhân vật → đủ cỡ iOS / Android / Steam / web, zip sẵn nộp store',
  'image-localize': 'Nút / logo / banner có chữ → bản Việt, Nhật, Trung, Hàn, Đức…: xoá chữ gốc, vẽ chữ dịch đúng font, màu, viền, vừa khung',
  'parallax-layers': 'Prompt cảnh → nền cuộn ngang 4–5 lớp (trời, xa, giữa, gần, tiền cảnh) lặp liền mạch, prefab Unity / Parallax2D Godot',
  'image-layers': 'Ảnh nhân vật / key art → 3–7 layer RGBA theo độ sâu, vẽ bù phần bị che, xuất PSD + PNG từng layer, prefab Unity / scene Godot',
  'autotile-terrain': 'Texture cỏ + texture đất / nước → bộ autotile 47 ô mép tự nhiên, khớp mép mọi kiểu xếp, TileSet terrain Godot / RuleTile Unity, chạy CPU',
  'bitmap-font': 'Bộ số / ASCII / chữ Việt có dấu → bitmap font gradient, viền, bóng, vát hoặc pixel art: atlas + .fnt BMFont, Godot đọc thẳng, chạy CPU',
  'bitmap-font-klein': 'Câu chất liệu ("molten gold", "icy crystal") → Klein tô ruột chữ, giữ đúng dáng glyph TTF → bitmap font .fnt + atlas cho Godot / Unity',
  'autotile-terrain-prompt': 'Texture cỏ + prompt nền (đất, nước, cát) → Z-Image vẽ nền → bộ autotile 47 ô khớp mép, TileSet terrain Godot / RuleTile Unity',
  'key-art-motion': 'Key art → teaser 5 giây chuyển động nhẹ (tóc, vải, hạt bay), lặp liền, MP4 + WebM + GIF + poster',
  'storyboard-animatic': 'Kịch bản cutscene + ảnh nhân vật → 4–12 shot giữ đúng nhân vật, thoại TTS mỗi người một giọng, Ken Burns + phụ đề → animatic MP4 + storyboard PDF'
}

export function recipePitch(id: string): string | undefined {
  return RECIPE_PITCH[id]
}

export const RECOMMENDED_RECIPES: ReadonlySet<string> = new Set(['sprite-8dir-3d-mv'])

export type RecipeGroup = '2d' | '3d' | 'character' | 'audio'

export const RECIPE_GROUP_LABEL: Record<RecipeGroup, string> = {
  '2d': '2D',
  '3d': '3D',
  character: 'Nhân vật',
  audio: 'Âm thanh'
}

// Sprites and rigs are characters to the user regardless of whether they pass through a 3D mesh.
export function recipeGroup(id: string): RecipeGroup {
  if (id.startsWith('audio-')) return 'audio'
  if (id.startsWith('sprite-') || id.startsWith('spine-') || id.startsWith('npc-') || id.includes('character')) return 'character'
  if (id.includes('3d') || id.includes('glb') || id.includes('mesh')) return '3d'
  return '2d'
}
