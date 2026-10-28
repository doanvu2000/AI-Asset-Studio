import { ApiError, type ListRunsQuery, type RecipeBody, type StudioApi, type VersionsQuery } from '../client'
import { FrameBatcher, type EventSink, type EventStream } from '../ws'
import type { CoreDiagnostics } from '../../../../preload/types'
import type { EnginePreviewJob, PreviewEngines } from '../enginePreview'
import {
  STEP_DONE_OK,
  type BatchCreateBody,
  type Binding,
  type BlobRef,
  type ExportPreset,
  type ProjectCreateBody,
  type ProjectPatchBody,
  type ProjectRow,
  type ProjectSummary,
  type ProjectWithSummary,
  type ToolKey,
  type ToolPath,
  type ToolPaths,
  type KindInfo,
  type RecipeDetail,
  type RecipeDoc,
  type RecipeSource,
  type SavedRecipe,
  type ValidateResult,
  type EventType,
  type GateResolution,
  type Lane,
  type Outputs,
  type RecipeInfo,
  type ResourceHealth,
  type RunDetail,
  type RunRow,
  type StartRunBody,
  type StepRow,
  type VersionRow
} from '../types'
import { renderArt, renderPreviewB64 } from './art'
import { renderWalkSheet } from './walkSheet'
import { mockStatsOverview } from './stats'
import type { StatsOverview, StatsQuery } from '../types'
import type { QaPart, QaRecomputeResult, QaResult } from '../types'
import type { GddProposal, ManifestFormat, ManifestRow, ManifestStatus, ManifestView } from '../types'
import type { SetupJob, SetupPlan, SetupStartBody } from '../types'
import type { ProjectQaAsset, ProjectQaFixResult, ProjectQaReport } from '../types'
import { MOCK_KINDS, MOCK_PRESETS, MOCK_TOOLS, mockValidate, stepDeps } from './catalog'
import { normalizeStyle, styleFills } from '@/composables/projectStyle'
import sampleGlbUrl from './assets/sample-prop.glb?url'
import sheetPngUrl from './assets/sample-sheet.png?url'
import sheetJsonUrl from './assets/sample-sheet.json?url'
import heroJsonUrl from './assets/hero.json?url'
import heroAtlasUrl from './assets/hero.atlas?url'
import heroPngUrl from './assets/hero.png?url'
import heroSetupUrl from './assets/hero-setup.webp?url'

interface Samples {
  glb: BlobRef
  sheet: BlobRef
  sheetMeta: BlobRef
  skeleton: BlobRef
  atlas: BlobRef
  texture: BlobRef
  setup: BlobRef
}

function mergeOutputs(list: Outputs[]): Outputs {
  const out: Outputs = {}
  for (const o of list) for (const [k, v] of Object.entries(o)) out[k] = [...(out[k] ?? []), ...v]
  return out
}

function uniqueBlobs(list: BlobRef[]): BlobRef[] {
  const seen = new Set<string>()
  return list.filter((b) => !seen.has(b.hash) && !!seen.add(b.hash))
}

interface MockStepDef {
  id: string
  kind: string
  lane: Lane
  deps: string[]
  in?: Record<string, Binding>
  params?: (inputs: Record<string, unknown>) => Record<string, unknown>
  raw?: Record<string, unknown>
}

interface MockRecipe {
  info: RecipeInfo
  defs: MockStepDef[]
  doc?: RecipeDoc
}

const LANE_LIMITS: Record<Lane, number> = { gpu: 1, blender: 1, cloud: 3, cpu: 4, none: Infinity }

// Mirrors the core recipes: normal steps are optional pass-throughs that come back empty when normals are off.
function normalChain(prefix: string, frames: string, pivot: string, mirror: boolean): MockStepDef[] {
  const src = mirror ? `${prefix}ndirs` : frames.split('.')[0]!
  return [
    ...(mirror
      ? [{ id: `${prefix}ndirs`, kind: 'op.mirror_dirs', lane: 'cpu' as Lane, deps: [frames.split('.')[0]!], in: { images: frames }, params: () => ({ count: 8, normal: true, optional: true }), raw: { count: 8, normal: true, optional: true } }]
      : []),
    { id: `${prefix}ndown`, kind: 'op.downscale', lane: 'cpu', deps: [src], in: { images: mirror ? `${src}.images` : frames }, params: () => ({ factor: 2, resample: 'box', normal: true, optional: true }), raw: { factor: 2, resample: 'box', normal: true, optional: true } },
    { id: `${prefix}nfx`, kind: 'op.sprite_fx', lane: 'cpu', deps: [`${prefix}ndown`], in: { images: `${prefix}ndown.images` }, params: () => ({ outline: 'none', width: 1, normal: true, optional: true }), raw: { outline: 'none', width: 1, normal: true, optional: true } },
    { id: `${prefix}nsheet`, kind: 'op.sheet', lane: 'cpu', deps: [`${prefix}nfx`], in: { images: `${prefix}nfx.images`, pivot }, params: () => ({ cols: 8, normal: true, optional: true }), raw: { cols: 8, normal: true, optional: true } }
  ]
}

const RECIPES: MockRecipe[] = [
  {
    info: {
      id: 'concept-2d',
      name: 'Concept 2D (Z-Image)',
      description: 'Text → N ảnh → chọn → tách nền → căn chỉnh',
      hash: 'b3c0ffee01',
      inputs: {
        prompt: { type: 'text', label: 'Prompt', required: true, help: 'Mô tả vật thể. Gõ "lỗi" để thử nhánh thất bại.' },
        count: { type: 'int', default: 4, min: 1, max: 8, label: 'Số biến thể' },
        size: { type: 'int', default: 512, options: [128, 256, 512, 1024], label: 'Kích thước' },
        seed: { type: 'int', default: -1, min: -1, label: 'Seed', help: '−1 là ngẫu nhiên. Seed cố định cho phép dùng lại kết quả cũ.' }
      },
      steps: []
    },
    defs: [
      { id: 'gen', kind: 'comfy.t2i_zimage_turbo', lane: 'gpu', deps: [], params: (i) => ({ '57:27.text': i.prompt, '57:13.batch_size': i.count, seed: i.seed }) },
      { id: 'pick', kind: 'gate.pick', lane: 'none', deps: ['gen'], params: () => ({ k: 1 }) },
      { id: 'rmbg', kind: 'comfy.remove_bg_birefnet', lane: 'gpu', deps: ['pick'] },
      { id: 'fit', kind: 'op.trim_pad_resize', lane: 'cpu', deps: ['rmbg'], params: (i) => ({ size: i.size, pad: 0.06 }) }
    ]
  },
  {
    info: {
      id: 'concept-2d-codex',
      name: 'Concept 2D (Codex)',
      description: 'Codex sinh ảnh nền trong suốt → chọn → căn chỉnh',
      hash: 'b3c0dex002',
      inputs: {
        prompt: { type: 'text', label: 'Prompt', required: true },
        count: { type: 'int', default: 2, min: 1, max: 4, label: 'Số biến thể' },
        transparent: { type: 'bool', default: true, label: 'Nền trong suốt' }
      },
      steps: []
    },
    defs: [
      { id: 'gen', kind: 'codex.image', lane: 'cloud', deps: [], params: (i) => ({ prompt: i.prompt, count: i.count }) },
      { id: 'pick', kind: 'gate.pick', lane: 'none', deps: ['gen'], params: () => ({ k: 1 }) },
      { id: 'fit', kind: 'op.trim_pad_resize', lane: 'cpu', deps: ['pick'], params: () => ({ size: 512, pad: 0.06 }) }
    ]
  },
  {
    info: {
      id: 'sprite-8dir',
      name: 'Sprite 8 hướng',
      description: 'Ảnh tham chiếu → mesh 3D → render 5 góc → lật thành 8 hướng → sheet',
      hash: 'b3c08d1r03',
      inputs: {
        ref: { type: 'image', label: 'Ảnh tham chiếu', required: true },
        elevation: { type: 'float', default: 30, min: 0, max: 60, step: 5, label: 'Góc nhìn (độ)' },
        res: { type: 'int', default: 256, options: [128, 256, 512], label: 'Độ phân giải' },
        preset: { type: 'preset', default: 'studio', label: 'Preset xuất' }
      },
      steps: []
    },
    defs: [
      { id: 'mesh', kind: 'comfy.i23d_hunyuan', lane: 'gpu', deps: [], in: { image: 'inputs.ref' } },
      { id: 'render', kind: 'blender.render_dirs', lane: 'blender', deps: ['mesh'], params: (i) => ({ elevation: i.elevation, count: 5, res: i.res }) },
      { id: 'mirror', kind: 'op.mirror_dirs', lane: 'cpu', deps: ['render'], params: () => ({ count: 8 }) },
      { id: 'approve', kind: 'gate.approve', lane: 'none', deps: ['mirror'] },
      { id: 'sheet', kind: 'op.sheet', lane: 'cpu', deps: ['approve'], params: (i) => ({ cols: 8, cell: 256, preset: i.preset }), raw: { cols: 8, cell: 256, preset: '${inputs.preset}' } }
    ]
  },
  {
    info: {
      id: 'prop-3d',
      name: 'Prop 3D (TRELLIS → GLB)',
      description: 'Text → concept → chọn → TRELLIS mesh có texture → Blender dọn mesh → kiểm GLB → turntable → duyệt',
      hash: 'b3c0pr0p3d',
      inputs: { prompt: { type: 'text', label: 'Prompt', required: true }, faces: { type: 'int', default: 30000, min: 2000, max: 200000, label: 'Số mặt' } },
      steps: []
    },
    defs: [
      { id: 'concept', kind: 'comfy.t2i_zimage_turbo', lane: 'gpu', deps: [] },
      { id: 'pick', kind: 'gate.pick', lane: 'none', deps: ['concept'], params: () => ({ k: 1 }) },
      { id: 'mesh', kind: 'comfy.img2mesh_trellis2_textured', lane: 'gpu', deps: ['pick'], in: { image: 'pick.images' } },
      { id: 'cleanup', kind: 'blender.cleanup', lane: 'blender', deps: ['mesh'], in: { mesh: 'mesh.meshes' }, params: (i) => ({ target_faces: i.faces, origin: 'feet' }) },
      { id: 'validate', kind: 'op.glb_validate', lane: 'cpu', deps: ['cleanup'], in: { mesh: 'cleanup.meshes' }, params: () => ({ require_textures: true }) },
      { id: 'turntable', kind: 'blender.render_dirs', lane: 'blender', deps: ['validate'], in: { mesh: 'validate.meshes' }, params: () => ({ count: 16, res: 256, elevation: 20 }) },
      { id: 'approve', kind: 'gate.approve', lane: 'none', deps: ['validate', 'turntable'], in: { meshes: 'validate.meshes', preview: 'turntable.images' } }
    ]
  },
  {
    info: {
      id: 'sprite-8dir-3d',
      name: 'Sprite 8 hướng (3D)',
      description: 'Text → concept → chọn → TRELLIS mesh → Blender dọn mesh → render 8 hướng ortho → thu nhỏ 2× → sprite sheet + JSON',
      hash: 'b3c08d3d05',
      inputs: { prompt: { type: 'text', label: 'Prompt', required: true }, preset: { type: 'preset', default: 'godot', label: 'Preset xuất' } },
      steps: []
    },
    defs: [
      { id: 'concept', kind: 'comfy.t2i_zimage_turbo', lane: 'gpu', deps: [] },
      { id: 'pick', kind: 'gate.pick', lane: 'none', deps: ['concept'], params: () => ({ k: 1 }) },
      { id: 'mesh', kind: 'comfy.img2mesh_trellis2_textured', lane: 'gpu', deps: ['pick'], in: { image: 'pick.images' } },
      { id: 'cleanup', kind: 'blender.cleanup', lane: 'blender', deps: ['mesh'], in: { mesh: 'mesh.meshes' } },
      { id: 'render', kind: 'blender.render_dirs', lane: 'blender', deps: ['cleanup'], in: { mesh: 'cleanup.meshes' }, params: () => ({ count: 8, elevation: 30, res: 256 }) },
      { id: 'dirs', kind: 'op.mirror_dirs', lane: 'cpu', deps: ['render'], in: { images: 'render.images' }, params: () => ({ count: 8 }) },
      { id: 'down', kind: 'op.downscale', lane: 'cpu', deps: ['dirs'], params: () => ({ factor: 2, resample: 'box' }) },
      { id: 'fx', kind: 'op.sprite_fx', lane: 'cpu', deps: ['down', 'render'], in: { images: 'down.images', pivot: 'render.meta' }, params: () => ({ shadow: 'none', outline: 'none', width: 1 }), raw: { shadow: 'none', outline: 'none', width: 1 } },
      { id: 'sheet', kind: 'op.sheet', lane: 'cpu', deps: ['fx'], in: { images: 'fx.images', pivot: 'render.meta' }, params: (i) => ({ cols: 8, preset: i.preset }), raw: { cols: 8, preset: '${inputs.preset}' } },
      ...normalChain('', 'render.normal_frames', 'render.meta', true)
    ]
  },
  {
    info: {
      id: 'sprite-8dir-anim',
      name: 'Sprite 8 hướng có hoạt ảnh (3D)',
      description: 'Text → mesh → render 8 hướng (sheet đứng yên) + Blender hoạt ảnh đi bộ 8 hướng → sheet hoạt ảnh (+ normal map)',
      hash: 'b3c0an1m8d',
      inputs: { prompt: { type: 'text', label: 'Prompt', required: true }, preset: { type: 'preset', default: 'godot', label: 'Preset xuất' } },
      steps: []
    },
    defs: [
      { id: 'concept', kind: 'comfy.t2i_zimage_turbo', lane: 'gpu', deps: [] },
      { id: 'pick', kind: 'gate.pick', lane: 'none', deps: ['concept'], params: () => ({ k: 1 }) },
      { id: 'mesh', kind: 'comfy.img2mesh_trellis2_textured', lane: 'gpu', deps: ['pick'], in: { image: 'pick.images' } },
      { id: 'cleanup', kind: 'blender.cleanup', lane: 'blender', deps: ['mesh'], in: { mesh: 'mesh.meshes' } },
      { id: 'render', kind: 'blender.render_dirs', lane: 'blender', deps: ['cleanup'], in: { mesh: 'cleanup.meshes' }, params: () => ({ count: 8, elevation: 30, res: 256 }) },
      { id: 'down', kind: 'op.downscale', lane: 'cpu', deps: ['render'], params: () => ({ factor: 2, resample: 'box' }) },
      { id: 'fx', kind: 'op.sprite_fx', lane: 'cpu', deps: ['down', 'render'], in: { images: 'down.images', pivot: 'render.meta' }, params: () => ({ shadow: 'none', outline: 'none', width: 1 }), raw: { shadow: 'none', outline: 'none', width: 1 } },
      { id: 'sheet', kind: 'op.sheet', lane: 'cpu', deps: ['fx'], in: { images: 'fx.images', pivot: 'render.meta' }, params: (i) => ({ cols: 8, preset: i.preset }), raw: { cols: 8, preset: '${inputs.preset}' } },
      { id: 'anim', kind: 'blender.animate_dirs', lane: 'blender', deps: ['cleanup'], in: { mesh: 'cleanup.meshes' }, params: () => ({ frames: 8, count: 8 }) },
      { id: 'anim_down', kind: 'op.downscale', lane: 'cpu', deps: ['anim'], in: { images: 'anim.images' }, params: () => ({ factor: 2, resample: 'box' }) },
      { id: 'anim_fx', kind: 'op.sprite_fx', lane: 'cpu', deps: ['anim_down', 'anim'], in: { images: 'anim_down.images', pivot: 'anim.meta' }, params: () => ({ shadow: 'none', outline: 'none', width: 1 }), raw: { shadow: 'none', outline: 'none', width: 1 } },
      { id: 'anim_sheet', kind: 'op.sheet', lane: 'cpu', deps: ['anim_fx'], in: { images: 'anim_fx.images', pivot: 'anim.meta' }, params: () => ({}) },
      ...normalChain('anim_', 'anim.normal_frames', 'anim.meta', false)
    ]
  },
  {
    info: {
      id: 'sprite-8dir-3d-pixel',
      name: 'Sprite 8 hướng pixel art (3D)',
      description: 'Text → concept → chọn → TRELLIS mesh → render 8 hướng → pixel art (bảng màu chung, viền 1px) → sprite sheet',
      hash: 'b3c0p1xe17',
      inputs: {
        prompt: { type: 'text', label: 'Prompt', required: true },
        pixel: { type: 'int', default: 64, options: [32, 48, 64, 96, 128], label: 'Cỡ pixel art (px)' },
        colors: { type: 'int', default: 16, options: [8, 12, 16, 24, 32], label: 'Số màu' },
        palette: { type: 'enum', default: 'auto', options: ['auto', 'pico8', 'sweetie16', 'db16', 'db32', 'endesga32'], label: 'Bảng màu' },
        outline: { type: 'enum', default: 'dark', options: ['dark', 'none', '#000000'], label: 'Viền ngoài' },
        dither: { type: 'enum', default: 'none', options: ['none', 'bayer'], label: 'Dither' },
        preset: { type: 'preset', default: 'pixel', label: 'Preset xuất' }
      },
      steps: []
    },
    defs: [
      { id: 'concept', kind: 'comfy.t2i_zimage_turbo', lane: 'gpu', deps: [] },
      { id: 'pick', kind: 'gate.pick', lane: 'none', deps: ['concept'], params: () => ({ k: 1 }) },
      { id: 'mesh', kind: 'comfy.img2mesh_trellis2_textured', lane: 'gpu', deps: ['pick'], in: { image: 'pick.images' } },
      { id: 'render', kind: 'blender.render_dirs', lane: 'blender', deps: ['mesh'], in: { mesh: 'mesh.meshes' }, params: () => ({ count: 8, elevation: 30, res: 256 }) },
      { id: 'pixel', kind: 'op.pixel_art', lane: 'cpu', deps: ['render'], params: (i) => ({ size: i.pixel, colors: i.colors, palette: i.palette, outline: i.outline, dither: i.dither }) },
      { id: 'fx', kind: 'op.sprite_fx', lane: 'cpu', deps: ['pixel', 'render'], in: { images: 'pixel.images', pivot: 'render.meta' }, params: () => ({ shadow: 'none', outline: 'none', width: 1, pixel: true }), raw: { shadow: 'none', outline: 'none', width: 1, pixel: true } },
      { id: 'sheet', kind: 'op.sheet', lane: 'cpu', deps: ['fx'], in: { images: 'fx.images', pivot: 'render.meta' }, params: (i) => ({ cols: 8, preset: i.preset }), raw: { cols: 8, preset: '${inputs.preset}' } }
    ]
  },
  {
    info: {
      id: 'texture-seamless',
      name: 'Texture liền mạch (Z-Image)',
      description: 'Prompt vật liệu → N ảnh Z-Image → chọn → làm liền mạch → chấm điểm mối nối + xem lát 3×3 → bản nhỏ, normal map, pixel art (tuỳ chọn) → duyệt',
      hash: 'b3c0t1le01',
      inputs: {
        prompt: { type: 'text', label: 'Vật liệu', required: true },
        count: { type: 'int', default: 4, min: 1, max: 8, label: 'Số biến thể' },
        size: { type: 'int', default: 1024, options: [256, 512, 1024], label: 'Cỡ texture (px)' },
        levels: { type: 'int', default: 2, min: 0, max: 4, label: 'Số bản nhỏ thêm (½, ¼…)' },
        normal: { type: 'bool', default: true, label: 'Tạo normal map' },
        pixel: { type: 'int', default: 0, options: [0, 32, 64, 128], label: 'Pixel art (px, 0 = tắt)' },
        review: { type: 'bool', default: true, label: 'Duyệt trước khi xong' }
      },
      steps: []
    },
    defs: [
      { id: 'gen', kind: 'comfy.t2i_zimage_turbo', lane: 'gpu', deps: [] },
      { id: 'pick', kind: 'gate.pick', lane: 'none', deps: ['gen'], params: () => ({ k: 1 }) },
      { id: 'seam', kind: 'op.seamless', lane: 'cpu', deps: ['pick'], in: { images: 'pick.images' } },
      { id: 'fit', kind: 'op.tile_resize', lane: 'cpu', deps: ['seam'], in: { images: 'seam.images' }, params: (i) => ({ size: i.size, levels: i.levels }) },
      { id: 'nmap', kind: 'op.tile_normal', lane: 'cpu', deps: ['fit'], in: { images: 'fit.images' }, params: (i) => ({ enabled: i.normal }) },
      { id: 'pix', kind: 'op.tile_pixel', lane: 'cpu', deps: ['fit'], in: { images: 'fit.images' }, params: (i) => ({ size: i.pixel }) },
      { id: 'check', kind: 'op.tile_check', lane: 'cpu', deps: ['fit', 'nmap', 'pix'], in: { images: 'fit.images', nmap: 'nmap.images', pixel: 'pix.images', sizes: 'fit.sizes' } },
      { id: 'approve', kind: 'gate.approve', lane: 'none', deps: ['check'], in: { images: 'check.images', preview: 'check.preview', report: 'check.report' } }
    ]
  },
  {
    info: {
      id: 'tileset-seamless',
      name: 'Tileset biến thể liền mạch (Z-Image)',
      description: 'Prompt vật liệu → N ảnh Z-Image → ảnh đầu làm liền mạch, các ảnh sau nối mép theo nó → chấm điểm + xem lát trộn 4×4 → normal map → duyệt',
      hash: 'b3c0t1le02',
      inputs: {
        prompt: { type: 'text', label: 'Vật liệu', required: true },
        count: { type: 'int', default: 4, min: 2, max: 8, label: 'Số biến thể' },
        size: { type: 'int', default: 512, options: [128, 256, 512, 1024], label: 'Cỡ ô (px)' },
        normal: { type: 'bool', default: true, label: 'Tạo normal map' },
        review: { type: 'bool', default: true, label: 'Duyệt trước khi xong' }
      },
      steps: []
    },
    defs: [
      { id: 'gen', kind: 'comfy.t2i_zimage_turbo', lane: 'gpu', deps: [] },
      { id: 'seam', kind: 'op.seamless', lane: 'cpu', deps: ['gen'], in: { images: 'gen.images' }, params: () => ({ match_edges: true, name: 'tile' }) },
      { id: 'fit', kind: 'op.tile_resize', lane: 'cpu', deps: ['seam'], in: { images: 'seam.images' }, params: (i) => ({ size: i.size, levels: 0 }) },
      { id: 'nmap', kind: 'op.tile_normal', lane: 'cpu', deps: ['fit'], in: { images: 'fit.images' }, params: (i) => ({ enabled: i.normal }) },
      { id: 'check', kind: 'op.tile_check', lane: 'cpu', deps: ['fit', 'nmap'], in: { images: 'fit.images', nmap: 'nmap.images' }, params: () => ({ grid: 4 }) },
      { id: 'approve', kind: 'gate.approve', lane: 'none', deps: ['check'], in: { images: 'check.images', preview: 'check.preview', report: 'check.report', atlas: 'check.atlas' } }
    ]
  },
  {
    info: {
      id: 'spine-character',
      name: 'Nhân vật Spine',
      description: 'Prompt → concept T-pose → chọn → tách nền → tách part → layers → Spine 4.2 (JSON + atlas + png) → duyệt',
      hash: 'b3c0sp1ne6',
      inputs: { prompt: { type: 'text', label: 'Nhân vật', required: true }, name: { type: 'text', default: 'character', label: 'Tên skeleton' } },
      steps: []
    },
    defs: [
      { id: 'concept', kind: 'comfy.t2i_zimage_turbo', lane: 'gpu', deps: [] },
      { id: 'pick', kind: 'gate.pick', lane: 'none', deps: ['concept'], params: () => ({ k: 1 }) },
      { id: 'rmbg', kind: 'comfy.remove_bg_birefnet', lane: 'gpu', deps: ['pick'], in: { image: 'pick.images' } },
      { id: 'parts', kind: 'op.parts', lane: 'cpu', deps: ['rmbg'], in: { image: 'rmbg.images' } },
      { id: 'layers', kind: 'op.layers', lane: 'cpu', deps: ['parts'], in: { image: 'rmbg.images', labels: 'parts.labels', parts: 'parts.meta' }, params: () => ({ rig: 'auto', fill: true }) },
      { id: 'export', kind: 'op.spine_export', lane: 'cpu', deps: ['layers'], in: { layers: 'layers.layers', meta: 'layers.meta' }, params: (i) => ({ name: i.name, animations: true }) },
      { id: 'approve', kind: 'gate.approve', lane: 'none', deps: ['export'], in: { skeleton: 'export.skeleton', atlas: 'export.atlas', texture: 'export.texture', preview: 'export.preview' } }
    ]
  }
]
for (const r of RECIPES) {
  r.info.steps = r.defs.map((d) => ({ id: d.id, kind: d.kind, lane: d.lane, deps: d.deps }))
  r.info.source = 'builtin'
}

const RAW_PARAMS: Record<string, Record<string, unknown>> = {
  'concept-2d/gen': { '57:27.text': '${inputs.prompt}, isolated on plain white background, centered, game asset', '57:13.batch_size': '${inputs.count}' },
  'concept-2d/pick': { k: 1 },
  'concept-2d/fit': { size: '${inputs.size}', pad: 0.06 },
  'concept-2d-codex/gen': { prompt: '${inputs.prompt}', count: '${inputs.count}', transparent: '${inputs.transparent}' },
  'concept-2d-codex/pick': { k: 1 },
  'sprite-8dir/render': { elevation: '${inputs.elevation}', count: 5, res: '${inputs.res}' }
}

function docOf(r: MockRecipe): RecipeDoc {
  if (r.doc) return structuredClone(r.doc)
  const last = [...r.defs].reverse().find((d) => d.raw?.normal !== true && d.raw?.optional !== true)!
  return {
    id: r.info.id,
    name: r.info.name,
    description: r.info.description,
    inputs: structuredClone(r.info.inputs),
    steps: r.defs.map((d) => {
      const params = d.raw ?? RAW_PARAMS[`${r.info.id}/${d.id}`] ?? {}
      const src = d.deps[0] ? `${d.deps[0]}.images` : undefined
      const inp = d.in ?? (src ? (d.kind === 'comfy.remove_bg_birefnet' ? { image: src } : { images: src }) : undefined)
      return {
        id: d.id,
        kind: d.kind,
        ...(inp ? { in: inp } : {}),
        ...(Object.keys(params).length ? { params } : {}),
        ...(d.kind === 'comfy.remove_bg_birefnet' ? { map: 'image' } : {})
      }
    }),
    outputs: { final: `${last.id}.${last.kind === 'op.sheet' ? 'sheet' : 'images'}` }
  }
}

function substituteInputs(v: unknown, inputs: Record<string, unknown>): unknown {
  if (typeof v === 'string') {
    const whole = /^\$\{inputs\.([\w-]+)\}$/.exec(v)
    if (whole) return inputs[whole[1]!]
    return v.replace(/\$\{inputs\.([\w-]+)\}/g, (_, n: string) => String(inputs[n] ?? ''))
  }
  if (Array.isArray(v)) return v.map((x) => substituteInputs(x, inputs))
  if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, substituteInputs(x, inputs)]))
  return v
}

function recipeFromDoc(doc: RecipeDoc, source: RecipeSource): MockRecipe {
  const lane = (kind: string): Lane => MOCK_KINDS.find((x) => x.kind === kind)?.lane ?? 'cpu'
  const defs: MockStepDef[] = doc.steps.map((s) => ({
    id: s.id,
    kind: s.kind,
    lane: lane(s.kind),
    deps: stepDeps(s),
    in: s.in,
    raw: s.params,
    params: (i) => substituteInputs(s.params ?? {}, i) as Record<string, unknown>
  }))
  const info: RecipeInfo = {
    id: doc.id,
    name: doc.name ?? doc.id,
    description: doc.description ?? '',
    inputs: doc.inputs ?? {},
    hash: Math.random().toString(16).slice(2, 12),
    source,
    steps: defs.map((d) => ({ id: d.id, kind: d.kind, lane: d.lane, deps: d.deps }))
  }
  return { info, defs, doc: structuredClone(doc) }
}

const TITLES = ['Kiếm pha lê', 'Bình máu đỏ', 'Rương gỗ cổ', 'Nấm phát sáng', 'Khiên đồng', 'Đá rune', 'Cuộn phép', 'Lồng đèn giấy', 'Mũ phù thuỷ', 'Trứng rồng']

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))
const now = (): number => Date.now() / 1000
const uid = (p: string): string => `${p}_${Math.random().toString(36).slice(2, 10)}`
const hex = (): string => Array.from(crypto.getRandomValues(new Uint8Array(32)), (b) => b.toString(16).padStart(2, '0')).join('')
const clone = <T>(v: T): T => structuredClone(v)

class Cancelled extends Error {}

export class MockCore implements StudioApi, EventStream {
  private seq = 0
  private sink: EventSink | null = null
  private batcher: FrameBatcher | null = null
  private blobs = new Map<string, { url: string; seed: number; ref: BlobRef }>()
  private runs = new Map<string, RunRow>()
  private steps = new Map<string, StepRow>()
  private versionMap = new Map<string, VersionRow>()
  private projectMap = new Map<string, ProjectRow>()
  private resourcesMap = new Map<string, ResourceHealth>()
  private cache = new Map<string, Outputs>()
  private laneBusy: Record<Lane, number> = { gpu: 0, blender: 0, cloud: 0, cpu: 0, none: 0 }
  private laneQueue: Record<Lane, Array<() => void>> = { gpu: [], blender: [], cloud: [], cpu: [], none: [] }
  private cancelled = new Set<string>()
  private builtins = new Map(RECIPES.map((r) => [r.info.id, r]))
  private userRecipes = new Map<string, MockRecipe>()
  private userPresets = new Map<string, ExportPreset>()
  private tools: ToolPath[] = MOCK_TOOLS.map((t) => ({ ...t }))
  private ready: Promise<void>
  private samples: Samples | null = null

  constructor() {
    this.ready = this.seed()
    window.setInterval(() => this.tickResources(), 1000)
  }

  start(sink: EventSink): void {
    this.sink = sink
    this.batcher = new FrameBatcher((events) => sink.batch(events))
    sink.state('connecting')
    void this.ready.then(() => {
      sink.state('open')
      this.flush()
      sink.resync()
    })
  }

  flush(): void {
    this.batcher?.flushNow()
  }

  stop(): void {
    this.sink?.state('closed')
    this.sink = null
  }

  private emit(type: EventType, data: unknown, run_id: string | null = null, step_id: string | null = null): void {
    this.seq += 1
    this.batcher?.push({ seq: this.seq, ts: now(), type, run_id, step_id, data: clone(data) as Record<string, unknown> })
  }

  // ---- blobs ----

  private async makeBlob(seed: number, opts: { size?: number; transparent?: boolean; name?: string }): Promise<BlobRef> {
    const size = opts.size ?? 512
    const blob = await renderArt({ seed, size, transparent: opts.transparent })
    const ref: BlobRef = { hash: hex(), mime: blob.type, size: blob.size, width: size, height: size, name: opts.name ?? `${seed}.${blob.type.split('/')[1]}` }
    this.blobs.set(ref.hash, { url: URL.createObjectURL(blob), seed, ref })
    return ref
  }

  private async sampleBlob(url: string, name: string, mime: string, w?: number, h?: number): Promise<BlobRef> {
    const size = (await (await fetch(url)).blob()).size
    const ref: BlobRef = { hash: hex(), mime, size, width: w ?? null, height: h ?? null, name }
    this.blobs.set(ref.hash, { url, seed: 7, ref })
    return ref
  }

  private putBlob(blob: Blob, name: string, w?: number, h?: number): BlobRef {
    const ref: BlobRef = { hash: hex(), mime: blob.type || 'application/octet-stream', size: blob.size, width: w ?? null, height: h ?? null, name }
    this.blobs.set(ref.hash, { url: URL.createObjectURL(blob), seed: 7, ref })
    return ref
  }

  private async walkOutputs(): Promise<{ color: Outputs; normal: Outputs }> {
    const w = await renderWalkSheet()
    const json = (o: unknown): Blob => new Blob([JSON.stringify(o, null, 1)], { type: 'application/json' })
    const mw = w.meta.width as number
    const mh = w.meta.height as number
    return {
      color: { sheet: [this.putBlob(w.color, 'knight_walk_sheet.png', mw, mh)], meta: [this.putBlob(json(w.meta), 'sheet.json')] },
      normal: { sheet: [this.putBlob(w.normal, 'knight_walk_sheet_normal.png', mw, mh)], meta: [this.putBlob(json(w.normalMeta), 'sheet_normal.json')] }
    }
  }

  private renamed(list: BlobRef[], name: (i: number) => string): BlobRef[] {
    return list.map((b, i) => {
      const ref: BlobRef = { ...b, hash: hex(), name: name(i) }
      this.blobs.set(ref.hash, { url: this.blobUrl(b.hash), seed: this.seedOf(b.hash), ref })
      return ref
    })
  }

  private meshReport(): BlobRef {
    const report = { attempts: [{ attempt: 1, hash: 'mock', error: 'blender_crashed' }], min_iou: 0.6, checked: true, passed: false, skipped: true, status: 'skipped' }
    return this.putBlob(new Blob([JSON.stringify(report)], { type: 'application/json' }), 'mesh_report.json')
  }

  private async loadSamples(): Promise<Samples> {
    const [glb, sheet, sheetMeta, skeleton, atlas, texture, setup] = await Promise.all([
      this.sampleBlob(sampleGlbUrl, 'crystal_pedestal_clean.glb', 'model/gltf-binary'),
      this.sampleBlob(sheetPngUrl, 'knight_sheet.png', 'image/png', 512, 64),
      this.sampleBlob(sheetJsonUrl, 'sheet.json', 'application/json'),
      this.sampleBlob(heroJsonUrl, 'hero.json', 'application/json'),
      this.sampleBlob(heroAtlasUrl, 'hero.atlas', 'text/plain'),
      this.sampleBlob(heroPngUrl, 'hero.png', 'image/png', 512, 512),
      this.sampleBlob(heroSetupUrl, 'setup_pose.webp', 'image/webp', 384, 384)
    ])
    return { glb, sheet, sheetMeta, skeleton, atlas, texture, setup }
  }

  private sampleOutputs(kind: string): Outputs | null {
    const x = this.samples
    if (!x) return null
    if (kind === 'comfy.img2mesh_trellis2_textured' || kind === 'blender.cleanup' || kind === 'op.glb_validate') return { meshes: [x.glb] }
    if (kind === 'op.sheet') return { sheet: [x.sheet], meta: [x.sheetMeta] }
    if (kind === 'op.spine_export') return { skeleton: [x.skeleton], atlas: [x.atlas], texture: [x.texture], preview: [x.setup] }
    return null
  }

  // Finished (or gate-paused) runs of the 3D / sprite / Spine recipes so every viewer has something to open.
  private seedShowcase(recipeId: string, title: string, created: number, outs: Record<string, Outputs>, publish: BlobRef[], awaitAt?: string): void {
    const recipe = this.builtins.get(recipeId)!
    const run = this.newRun(recipe, { prompt: title }, title, created)
    const steps = this.createSteps(run, recipe)
    const byKey = new Map(steps.map((s) => [s.key, s]))
    let t = created
    for (const s of steps) {
      s.inputs = mergeOutputs(s.deps.map((d) => byKey.get(d)?.outputs ?? {}))
      s.started_at = t
      if (s.key === awaitAt) {
        s.status = 'awaiting_human'
        s.meta = { gate: 'approve' }
        run.status = 'awaiting_human'
        return
      }
      t += s.lane === 'gpu' || s.lane === 'blender' ? 50 + Math.random() * 90 : 3 + Math.random() * 25
      s.status = 'succeeded'
      s.ended_at = t
      s.progress = 1
      s.outputs = outs[s.key] ?? (s.kind === 'gate.approve' ? s.inputs : {})
    }
    run.status = 'succeeded'
    run.ended_at = t
    for (const b of publish) {
      const from = [...steps].reverse().find((s) => Object.values(s.outputs).some((l) => l.some((x) => x.hash === b.hash))) ?? steps[steps.length - 1]!
      const v: VersionRow = { id: uid('ver'), project_id: 'default', asset_name: title, run_id: run.id, step_id: from.id, blob: b, status: 'draft', created_at: t }
      this.versionMap.set(v.id, v)
    }
  }

  private seedOf(hash: string): number {
    return this.blobs.get(hash)?.seed ?? 7
  }

  blobUrl(hash: string): string {
    return this.blobs.get(hash)?.url ?? ''
  }

  thumbUrl(hash: string): string {
    return this.blobUrl(hash)
  }

  async uploadBlob(file: File): Promise<BlobRef> {
    await sleep(350)
    const ref: BlobRef = { hash: hex(), mime: file.type || 'application/octet-stream', size: file.size, name: file.name }
    this.blobs.set(ref.hash, { url: URL.createObjectURL(file), seed: Math.floor(Math.random() * 1e6), ref })
    return ref
  }

  async blobInfo(hash: string): Promise<BlobRef> {
    const b = this.blobs.get(hash)
    if (!b) throw new ApiError('not_found', `unknown blob: ${hash}`, 404)
    return b.ref
  }

  // ---- seed data ----

  private async seed(): Promise<void> {
    this.resourcesMap.set('comfyui', { name: 'comfyui', status: 'up', detail: { vram_free_mb: 13800, vram_total_mb: 16376, version: '0.3.68', managed: true, pid: 18244 } })
    this.resourcesMap.set('blender', { name: 'blender', status: 'down', detail: { version: '4.5.3', managed: true } })
    this.resourcesMap.set('codex', { name: 'codex', status: 'up', detail: { version: 'codex-cli 0.48' } })
    this.resourcesMap.set('claude', { name: 'claude', status: 'up', detail: { version: 'claude 2.1' } })

    const pool: BlobRef[] = []
    for (let i = 0; i < 18; i++) pool.push(await this.makeBlob(1000 + i * 37, { size: 256, transparent: true }))

    const statuses: RunRow['status'][] = ['succeeded', 'succeeded', 'succeeded', 'failed', 'succeeded', 'cancelled', 'interrupted', 'succeeded']
    const t0 = now()
    for (let i = 0; i < 64; i++) {
      const recipe = RECIPES[i % 5 === 0 ? 1 : 0]!
      const status = statuses[i % statuses.length]!
      const created = t0 - 3600 * (i + 1) - Math.random() * 1800
      const run = this.newRun(recipe, { prompt: TITLES[i % TITLES.length], count: 4, size: 512, seed: -1 }, TITLES[i % TITLES.length]!, created)
      run.status = status
      run.ended_at = created + 40 + Math.random() * 90
      if (status === 'failed') run.error = 'comfy_node_errors'
      const steps = this.createSteps(run, recipe)
      let t = created
      for (const s of steps) {
        const img = pool[(i + s.order) % pool.length]!
        const failedHere = status === 'failed' && s.key === 'rmbg'
        const interruptedHere = status === 'interrupted' && s.order === 0
        const cancelledHere = status === 'cancelled' && s.order === 2
        const prior = steps.filter((x) => x.order < s.order)
        const blocked = prior.some((x) => !STEP_DONE_OK.has(x.status))
        s.started_at = t
        t += 5 + Math.random() * 20
        if (blocked) {
          s.status = 'skipped'
          s.started_at = null
        } else if (failedHere) {
          s.status = 'failed'
          s.error = { code: 'comfy_node_errors', message: 'BiRefNet: CUDA out of memory khi tách nền (cần ~2.1 GB VRAM trống).' }
          s.ended_at = t
        } else if (interruptedHere) {
          s.status = 'failed'
          s.error = { code: 'interrupted', message: 'Core khởi động lại khi bước đang chạy.', retryable: true }
          s.ended_at = t
        } else if (cancelledHere) {
          s.status = 'cancelled'
          s.ended_at = t
        } else {
          s.status = s.key === 'fit' && i % 3 === 0 ? 'cached' : 'succeeded'
          s.ended_at = t
          s.outputs = { images: s.key === 'gen' ? [img, pool[(i + 5) % pool.length]!, pool[(i + 9) % pool.length]!] : [img] }
        }
      }
      if (status === 'succeeded') {
        const fit = steps[steps.length - 1]!
        for (const b of fit.outputs.images ?? []) {
          const v: VersionRow = { id: uid('ver'), project_id: 'default', asset_name: run.title, run_id: run.id, step_id: fit.id, blob: b, status: i % 4 === 0 ? 'draft' : i % 7 === 0 ? 'rejected' : 'approved', created_at: run.ended_at! }
          this.versionMap.set(v.id, v)
        }
      }
    }

    const x = (this.samples = await this.loadSamples())
    const propOuts: Record<string, Outputs> = { concept: { images: pool.slice(6, 10) }, pick: { images: [pool[6]!] }, mesh: { meshes: [x.glb] }, cleanup: { meshes: [x.glb] }, validate: { meshes: [x.glb] }, turntable: { images: pool.slice(0, 6) } }
    this.seedShowcase('prop-3d', 'Bệ pha lê', t0 - 900, propOuts, [x.glb])
    const walk = await this.walkOutputs()
    const dirName = (suffix: string) => (i: number) => `dir_${String(i * 45).padStart(3, '0')}${suffix}.png`
    const colorDirs = this.renamed(pool.slice(0, 8), dirName(''))
    const normalDirs = this.renamed(pool.slice(0, 8), dirName('_n'))
    const smallNormals = this.renamed(normalDirs, dirName('_down_n'))
    this.seedShowcase(
      'sprite-8dir-3d',
      'Hiệp sĩ đi bộ (3 clip + normal)',
      t0 - 1200,
      {
        concept: { images: pool.slice(10, 14) },
        pick: { images: [pool[10]!] },
        mesh: { meshes: [x.glb], report: [this.meshReport()] },
        cleanup: { meshes: [x.glb] },
        render: { images: colorDirs.slice(0, 5), normal_frames: normalDirs.slice(0, 5) },
        dirs: { images: colorDirs },
        down: { images: colorDirs },
        sheet: walk.color,
        ndirs: { images: normalDirs },
        ndown: { images: smallNormals },
        fx: { images: colorDirs },
        nfx: { images: smallNormals },
        nsheet: walk.normal
      },
      [...Object.values(walk.color).flat(), ...Object.values(walk.normal).flat()]
    )
    this.seedShowcase('sprite-8dir-3d', 'Hiệp sĩ 8 hướng', t0 - 1500, { concept: { images: pool.slice(10, 14) }, pick: { images: [pool[10]!] }, mesh: { meshes: [x.glb] }, cleanup: { meshes: [x.glb] }, render: { images: pool.slice(0, 8) }, dirs: { images: pool.slice(0, 8) }, down: { images: pool.slice(0, 8) }, sheet: { sheet: [x.sheet], meta: [x.sheetMeta] } }, [x.sheet, x.sheetMeta])
    const animFrames = (suffix: string) => this.renamed(pool.slice(0, 8), (i) => `walk_dir_${String(i * 45).padStart(3, '0')}_f00${suffix}.png`)
    const animNormals = animFrames('_n')
    this.seedShowcase(
      'sprite-8dir-anim',
      'Hiệp sĩ hoạt ảnh (sheet tĩnh + đi bộ + normal)',
      t0 - 1350,
      {
        concept: { images: pool.slice(10, 14) },
        pick: { images: [pool[10]!] },
        mesh: { meshes: [x.glb] },
        cleanup: { meshes: [x.glb] },
        render: { images: colorDirs },
        down: { images: colorDirs },
        sheet: { sheet: [x.sheet], meta: [x.sheetMeta] },
        anim: { images: animFrames(''), normal_frames: animNormals },
        anim_down: { images: animFrames('') },
        anim_sheet: walk.color,
        fx: { images: colorDirs },
        anim_fx: { images: animFrames('') },
        anim_ndown: { images: this.renamed(animNormals, (i) => `walk_dir_${String(i * 45).padStart(3, '0')}_f00_down_n.png`) },
        anim_nfx: { images: this.renamed(animNormals, (i) => `walk_dir_${String(i * 45).padStart(3, '0')}_f00_down_n.png`) },
        anim_nsheet: walk.normal
      },
      [x.sheet, x.sheetMeta, ...Object.values(walk.color).flat(), ...Object.values(walk.normal).flat()]
    )
    const spineOuts: Record<string, Outputs> = { concept: { images: pool.slice(2, 6) }, pick: { images: [pool[2]!] }, rmbg: { images: [pool[2]!] }, parts: { preview: [pool[3]!] }, layers: { preview: [pool[4]!] }, export: { skeleton: [x.skeleton], atlas: [x.atlas], texture: [x.texture], preview: [x.setup] } }
    this.seedShowcase('spine-character', 'Hiệp sĩ Spine', t0 - 2100, spineOuts, [x.skeleton, x.atlas, x.texture, x.setup])
    this.seedShowcase('spine-character', 'Hiệp sĩ Spine (chờ duyệt)', t0 - 300, spineOuts, [], 'approve')
    this.seedShowcase('prop-3d', 'Bệ pha lê (chờ duyệt)', t0 - 240, propOuts, [], 'approve')

    this.seedProjects(t0)

    // A live run occupying the GPU so a new run visibly waits for the lane, plus one gate waiting for a pick.
    void this.startRun({ recipe_id: 'concept-2d', inputs: { prompt: 'Kiếm pha lê xanh, low-poly', count: 4, size: 512, seed: -1 }, title: 'Kiếm pha lê xanh' })
    const gateRun = await this.startRun({ recipe_id: 'concept-2d-codex', inputs: { prompt: 'Bình thuốc tím phát sáng', count: 4, transparent: true }, title: 'Bình thuốc tím' })
    void gateRun
  }

  private seedProjects(t0: number): void {
    const mk = (name: string, description: string, style: Partial<ProjectRow['style']>, created: number, archived = false): ProjectRow => {
      const p: ProjectRow = { id: uid('prj'), name, description, style: normalizeStyle(style), created_at: created, updated_at: created, archived }
      this.projectMap.set(p.id, p)
      return p
    }
    const forest = mk(
      'Rừng Tiên — pixel',
      'Bộ nhân vật + vật phẩm pixel art 64px cho game nhập vai rừng thần tiên.',
      { prompt_prefix: 'pixel art, 16-bit', prompt_suffix: 'whimsical forest fantasy, soft greens', negative: 'blurry, text, watermark', preset: 'pixel', pixel: { size: 64, palette: 'sweetie16', colors: 16, outline: 'dark', dither: 'none' }, notes: 'Giữ bảng màu Sweetie 16 cho mọi asset.' },
      t0 - 86400 * 6
    )
    const knight = mk(
      'Hiệp sĩ Bóng Đêm',
      'Nhân vật và đạo cụ dark fantasy, xuất cho Godot.',
      { prompt_prefix: 'dark fantasy', prompt_suffix: 'painterly, rim light, muted palette', negative: 'cartoon, low quality', preset: 'godot' },
      t0 - 86400 * 12
    )
    mk('Thử nghiệm cũ', 'Dự án đã lưu trữ.', { prompt_suffix: 'flat vector style' }, t0 - 86400 * 40, true)
    const runs = [...this.runs.values()].sort((a, b) => b.created_at - a.created_at)
    runs.forEach((r, i) => {
      const p = r.recipe_id.startsWith('sprite') || r.title.includes('Hiệp sĩ') ? knight : i % 4 === 1 ? forest : i % 6 === 2 ? knight : null
      if (!p) return
      r.project_id = p.id
      r.params = { ...r.params, $meta: { ...((r.params.$meta as object | undefined) ?? {}), project_id: p.id, project_applied: p === forest ? ['prompt', 'preset'] : ['prompt'] } }
    })
    for (const v of this.versionMap.values()) v.project_id = this.runs.get(v.run_id)?.project_id ?? 'default'
  }

  private applyProject(projectId: string | undefined, recipe: MockRecipe, inputs: Record<string, unknown>): { inputs: Record<string, unknown>; applied: string[]; projectId: string } {
    if (!projectId || projectId === 'default') return { inputs, applied: [], projectId: 'default' }
    const p = this.projectMap.get(projectId)
    if (!p) throw new ApiError('not_found', `unknown project: ${projectId}`, 404)
    const out = { ...inputs }
    const applied: string[] = []
    for (const f of styleFills(recipe.info.inputs, p.style)) {
      const cur = out[f.key]
      if (f.wraps) {
        if (typeof cur !== 'string' || !cur.trim()) continue
        const low = cur.toLowerCase()
        const pre = p.style.prompt_prefix.trim()
        const suf = p.style.prompt_suffix.trim()
        const next = [pre && !low.includes(pre.toLowerCase()) ? pre : '', cur.trim(), suf && !low.includes(suf.toLowerCase()) ? suf : ''].filter(Boolean).join(', ')
        if (next !== cur) {
          out[f.key] = next
          applied.push(f.key)
        }
      } else if (cur === undefined || cur === null || cur === '') {
        out[f.key] = f.value
        applied.push(f.key)
      }
    }
    return { inputs: out, applied, projectId }
  }

  private newRun(recipe: MockRecipe, inputs: Record<string, unknown>, title: string, created = now(), params: Record<string, unknown> = {}): RunRow {
    const run: RunRow = { id: uid('run'), project_id: 'default', recipe_id: recipe.info.id, recipe_hash: recipe.info.hash, title, inputs, params, status: 'running', error: null, created_at: created, ended_at: null }
    this.runs.set(run.id, run)
    return run
  }

  private createSteps(run: RunRow, recipe: MockRecipe): StepRow[] {
    return recipe.defs.map((d, order) => {
      const s: StepRow = { id: uid('step'), run_id: run.id, key: d.id, kind: d.kind, lane: d.lane, status: 'pending', attempt: 1, deps: d.deps, params: d.params?.(run.inputs) ?? {}, inputs: {}, outputs: {}, cache_key: null, error: null, progress: null, meta: {}, order, started_at: null, ended_at: null }
      this.steps.set(s.id, s)
      return s
    })
  }

  private stepsOf(runId: string): StepRow[] {
    return [...this.steps.values()].filter((s) => s.run_id === runId).sort((a, b) => a.order - b.order)
  }

  private patchStep(s: StepRow, patch: Partial<StepRow>): void {
    Object.assign(s, patch)
    this.emit('step.updated', s, s.run_id, s.id)
  }

  private patchRun(r: RunRow, patch: Partial<RunRow>): void {
    Object.assign(r, patch)
    this.emit('run.updated', { status: r.status, error: r.error, ended_at: r.ended_at }, r.id)
  }

  // ---- lanes ----

  private tryAcquire(lane: Lane): boolean {
    if (this.laneBusy[lane] < LANE_LIMITS[lane]) {
      this.laneBusy[lane] += 1
      return true
    }
    return false
  }

  private acquire(lane: Lane): Promise<void> {
    return new Promise((resolve) => this.laneQueue[lane].push(() => resolve()))
  }

  private release(lane: Lane): void {
    const next = this.laneQueue[lane].shift()
    if (next) next()
    else this.laneBusy[lane] -= 1
  }

  // ---- engine ----

  private advance(runId: string): void {
    const run = this.runs.get(runId)
    if (!run || !['running', 'awaiting_human'].includes(run.status)) return
    const steps = this.stepsOf(runId)
    const byKey = new Map(steps.map((s) => [s.key, s]))

    if (steps.some((s) => s.status === 'failed')) {
      for (const s of steps) if (s.status === 'pending') this.patchStep(s, { status: 'skipped' })
      if (!steps.some((s) => ['running', 'waiting_resource', 'ready'].includes(s.status))) {
        const failed = steps.find((s) => s.status === 'failed')!
        this.patchRun(run, { status: 'failed', error: failed.error?.code ?? 'failed', ended_at: now() })
      }
      return
    }

    for (const s of steps) {
      if (s.status !== 'pending') continue
      if (s.deps.every((d) => STEP_DONE_OK.has(byKey.get(d)?.status ?? 'pending'))) {
        s.inputs = mergeOutputs(s.deps.map((d) => byKey.get(d)?.outputs ?? {}))
        void this.execStep(s)
      }
    }

    const active = steps.some((s) => ['ready', 'waiting_resource', 'running'].includes(s.status))
    const awaiting = steps.some((s) => s.status === 'awaiting_human')
    if (steps.every((s) => STEP_DONE_OK.has(s.status))) {
      this.patchRun(run, { status: 'succeeded', ended_at: now() })
      const last = steps[steps.length - 1]!
      for (const b of uniqueBlobs(Object.values(last.outputs).flat())) {
        const v: VersionRow = { id: uid('ver'), project_id: run.project_id, asset_name: run.title || run.recipe_id, run_id: run.id, step_id: last.id, blob: b, status: 'draft', created_at: now() }
        this.versionMap.set(v.id, v)
        this.emit('version.updated', v, run.id)
      }
    } else if (!active && awaiting && run.status !== 'awaiting_human') {
      this.patchRun(run, { status: 'awaiting_human' })
    } else if (active && run.status === 'awaiting_human') {
      this.patchRun(run, { status: 'running' })
    }
  }

  private cacheKey(s: StepRow): string | null {
    const run = this.runs.get(s.run_id)!
    if (s.lane === 'none' || s.kind.startsWith('codex.')) return null
    if (s.key === 'gen' && Number(run.inputs.seed ?? -1) < 0) return null
    const inputs = (s.inputs.images ?? []).map((b) => b.hash).sort().join(',')
    return `${s.kind}|${JSON.stringify(s.params)}|${inputs}`
  }

  private checkCancel(s: StepRow): void {
    if (this.cancelled.has(s.run_id)) throw new Cancelled()
  }

  private async execStep(s: StepRow): Promise<void> {
    this.patchStep(s, { status: 'ready' })
    await sleep(180)
    if (this.cancelled.has(s.run_id)) return

    const key = this.cacheKey(s)
    if (key && this.cache.has(key)) {
      this.patchStep(s, { status: 'cached', cache_key: key, outputs: clone(this.cache.get(key)!), started_at: now(), ended_at: now(), progress: 1 })
      this.advance(s.run_id)
      return
    }

    if (s.lane === 'none') {
      const candidates = s.kind === 'gate.pick' ? (s.inputs.images ?? []) : uniqueBlobs(Object.values(s.inputs).flat())
      const kind = s.kind === 'gate.pick' ? 'pick' : 'approve'
      this.patchStep(s, { status: 'awaiting_human', started_at: now(), meta: { gate: kind } })
      this.emit('gate.awaiting', { kind, k: kind === 'pick' ? Number(s.params.k ?? 0) : 0, candidates }, s.run_id, s.id)
      this.advance(s.run_id)
      return
    }

    if (!this.tryAcquire(s.lane)) {
      this.patchStep(s, { status: 'waiting_resource', meta: { waiting_for: s.lane } })
      await this.acquire(s.lane)
    }
    try {
      this.checkCancel(s)
      this.patchStep(s, { status: 'running', started_at: now(), progress: 0, meta: {} })
      const outputs = await this.simulate(s)
      if (key) this.cache.set(key, outputs)
      this.patchStep(s, { status: 'succeeded', outputs, progress: 1, ended_at: now(), cache_key: key })
    } catch (e) {
      if (e instanceof Cancelled) {
        this.patchStep(s, { status: 'cancelled', ended_at: now() })
      } else {
        const err = e as { code?: string; message?: string }
        this.patchStep(s, { status: 'failed', ended_at: now(), error: { code: err.code ?? 'mock_error', message: err.message ?? String(e), retryable: true } })
      }
    } finally {
      this.release(s.lane)
    }
    this.advance(s.run_id)
  }

  private progress(s: StepRow, value: number, max: number, node: string, label?: string): void {
    s.progress = max ? value / max : null
    this.emit('step.progress', { value, max, node, label: label ?? null }, s.run_id, s.id)
  }

  private log(s: StepRow, msg: string, level = 'info'): void {
    this.emit('step.log', { level, msg }, s.run_id, s.id)
  }

  private async simulate(s: StepRow): Promise<Outputs> {
    const run = this.runs.get(s.run_id)!
    const inImgs = s.inputs.images ?? []
    const size = Number(run.inputs.size ?? run.inputs.res ?? 512)
    // Normals are off in live mock runs; optional normal steps pass through empty like the core does.
    if (s.params.optional === true) {
      await sleep(150)
      return {}
    }

    if (s.kind === 'comfy.t2i_zimage_turbo') {
      const count = Number(run.inputs.count ?? 4)
      const seedBase = Number(run.inputs.seed ?? -1) >= 0 ? Number(run.inputs.seed) : Math.floor(Math.random() * 1e6)
      this.progress(s, 0, 1, 'CLIPTextEncode', 'Mã hoá prompt')
      await sleep(600)
      this.checkCancel(s)
      const total = 9
      for (let i = 1; i <= total; i++) {
        await sleep(480)
        this.checkCancel(s)
        this.progress(s, i, total, 'KSampler', `Lấy mẫu ${i}/${total}`)
        void renderPreviewB64(seedBase, 1 - i / total).then((b64) => this.emit('step.preview', { mime: 'image/jpeg', b64 }, s.run_id, s.id))
      }
      this.progress(s, total, total, 'VAEDecode', 'Giải mã ảnh')
      await sleep(500)
      const out: BlobRef[] = []
      for (let i = 0; i < count; i++) out.push(await this.makeBlob(seedBase + i * 101, { size: 512 }))
      this.log(s, `Sinh ${count} ảnh, seed ${seedBase}`)
      return { images: out }
    }

    if (s.kind === 'codex.image') {
      const count = Number(run.inputs.count ?? 2)
      this.log(s, `codex exec: ${count} tiến trình song song`)
      await sleep(4200)
      this.checkCancel(s)
      const out: BlobRef[] = []
      for (let i = 0; i < count; i++) out.push(await this.makeBlob(Math.floor(Math.random() * 1e6), { size: 512, transparent: true }))
      return { images: out }
    }

    if (s.kind === 'comfy.remove_bg_birefnet') {
      const prompt = String(run.inputs.prompt ?? '').toLowerCase()
      const out: BlobRef[] = []
      for (let i = 0; i < inImgs.length; i++) {
        this.progress(s, i, inImgs.length, 'BiRefNet', `Tách nền ${i + 1}/${inImgs.length}`)
        await sleep(1100)
        this.checkCancel(s)
        if (s.attempt === 1 && (prompt.includes('lỗi') || prompt.includes('fail'))) {
          throw Object.assign(new Error('BiRefNet: CUDA out of memory khi tách nền (cần ~2.1 GB VRAM trống). Đóng ứng dụng khác dùng GPU rồi thử lại.'), { code: 'comfy_node_errors' })
        }
        out.push(await this.makeBlob(this.seedOf(inImgs[i]!.hash), { size: 512, transparent: true }))
      }
      this.progress(s, inImgs.length, inImgs.length, 'SaveImage')
      return { images: out }
    }

    if (s.kind === 'op.trim_pad_resize') {
      await sleep(450)
      const out: BlobRef[] = []
      for (const b of inImgs) out.push(await this.makeBlob(this.seedOf(b.hash), { size, transparent: true }))
      return { images: out }
    }

    if (s.kind === 'op.mirror_dirs') {
      await sleep(400)
      const out: BlobRef[] = []
      for (let i = 0; i < 8; i++) out.push(await this.makeBlob(this.seedOf(inImgs[i % Math.max(1, inImgs.length)]?.hash ?? '') + i, { size, transparent: true }))
      return { images: out }
    }

    const n = s.kind === 'blender.render_dirs' ? 5 : 1
    const steps = 6
    for (let i = 1; i <= steps; i++) {
      await sleep(500)
      this.checkCancel(s)
      this.progress(s, i, steps, s.kind.split('.')[1] ?? s.kind)
    }
    if (s.params.normal === true) return {}
    const sample = this.sampleOutputs(s.kind)
    if (sample) return sample
    const out: BlobRef[] = []
    for (let i = 0; i < n; i++) out.push(await this.makeBlob(Math.floor(Math.random() * 1e6), { size, transparent: true }))
    return { images: out }
  }

  private tickResources(): void {
    const comfy = this.resourcesMap.get('comfyui')
    if (!comfy || comfy.status === 'down') return
    const busy = this.laneBusy.gpu > 0
    const total = Number(comfy.detail.vram_total_mb)
    const target = busy ? total - 2400 - 9800 - Math.random() * 700 : total - 2400 - Math.random() * 300
    const next: ResourceHealth = { ...comfy, status: busy ? 'busy' : 'up', detail: { ...comfy.detail, vram_free_mb: Math.round(target) } }
    if (next.status !== comfy.status || Math.abs(Number(next.detail.vram_free_mb) - Number(comfy.detail.vram_free_mb)) > 60) {
      this.resourcesMap.set('comfyui', next)
      this.emit('resource.updated', next)
    }
  }

  // ---- API ----

  async health(): Promise<{ ok: boolean; version: string }> {
    return { ok: true, version: 'mock' }
  }

  async resources(): Promise<ResourceHealth[]> {
    await this.ready
    return clone([...this.resourcesMap.values()])
  }

  async startResource(name: string): Promise<ResourceHealth> {
    const r = this.resourcesMap.get(name)
    if (!r) throw new ApiError('not_found', `Không có tài nguyên ${name}`, 404)
    const starting: ResourceHealth = { ...r, status: 'starting' }
    this.resourcesMap.set(name, starting)
    this.emit('resource.updated', starting)
    window.setTimeout(() => {
      const up: ResourceHealth = { ...starting, status: 'up', detail: { ...starting.detail, pid: 20000 + Math.floor(Math.random() * 9000) } }
      this.resourcesMap.set(name, up)
      this.emit('resource.updated', up)
    }, 1600)
    return clone(starting)
  }

  async stopResource(name: string, force = false): Promise<ResourceHealth> {
    const r = this.resourcesMap.get(name)
    if (!r) throw new ApiError('not_found', `Không có tài nguyên ${name}`, 404)
    const busy = [...this.steps.values()].some((s) => s.status === 'running' && s.lane === (name === 'comfyui' ? 'gpu' : name))
    if (busy && !force) throw new ApiError('resource_busy', `${name} đang chạy một bước`, 409)
    const down: ResourceHealth = { ...r, status: 'down', detail: { ...r.detail, pid: undefined } }
    this.resourcesMap.set(name, down)
    this.emit('resource.updated', down)
    return clone(down)
  }

  private recipe(id: string): MockRecipe | undefined {
    return this.userRecipes.get(id) ?? this.builtins.get(id)
  }

  async recipes(): Promise<RecipeInfo[]> {
    await sleep(80)
    const ids = new Set([...this.builtins.keys(), ...this.userRecipes.keys()])
    return clone([...ids].map((id) => this.recipe(id)!.info))
  }

  async kinds(): Promise<KindInfo[]> {
    await sleep(60)
    return clone(MOCK_KINDS)
  }

  async getRecipe(id: string): Promise<RecipeDetail> {
    await sleep(80)
    const r = this.recipe(id)
    if (!r) throw new ApiError('not_found', `unknown recipe: ${id}`, 404)
    const doc = docOf(r)
    return clone({ info: r.info, source: r.info.source ?? 'builtin', has_builtin: this.builtins.has(id), yaml: JSON.stringify(doc, null, 2), doc })
  }

  private docFromBody(body: RecipeBody): RecipeDoc {
    if ('doc' in body) return clone(body.doc)
    try {
      return JSON.parse(body.yaml) as RecipeDoc
    } catch {
      throw new ApiError('invalid', 'Chế độ mô phỏng chỉ đọc được YAML dạng JSON', 400)
    }
  }

  async validateRecipe(body: RecipeBody): Promise<ValidateResult> {
    await sleep(120)
    const doc = this.docFromBody(body)
    const issues = mockValidate(doc, MOCK_KINDS)
    const ok = !issues.some((i) => i.level === 'error')
    return { ok, issues, info: ok ? recipeFromDoc(doc, 'user').info : null }
  }

  async saveRecipe(id: string, body: RecipeBody): Promise<SavedRecipe> {
    await sleep(160)
    const doc = { ...this.docFromBody(body), id }
    const issues = mockValidate(doc, MOCK_KINDS)
    if (issues.some((i) => i.level === 'error')) throw new ApiError('invalid_recipe', issues[0]!.message, 422, issues)
    const r = recipeFromDoc(doc, this.builtins.has(id) ? 'override' : 'user')
    this.userRecipes.set(id, r)
    return clone({ info: r.info, source: r.info.source!, has_builtin: this.builtins.has(id), issues })
  }

  async deleteRecipe(id: string): Promise<{ ok: boolean; restored_builtin: boolean }> {
    await sleep(100)
    if (!this.userRecipes.delete(id)) throw new ApiError('not_found', `no user recipe '${id}'`, 404)
    return { ok: true, restored_builtin: this.builtins.has(id) }
  }

  async translate(texts: string[]): Promise<{ texts: string[] }> {
    await sleep(600)
    return { texts: texts.map((t) => (/[ăâđêôơưàáạảãèéẹẻẽìíịỉĩòóọỏõùúụủũỳýỵỷỹ]/i.test(t) ? `[EN] ${t}` : t)) }
  }

  async startBatch(body: BatchCreateBody): Promise<{ batch_id: string; run_ids: string[] }> {
    if (!this.recipe(body.recipe_id)) throw new ApiError('not_found', `unknown recipe: ${body.recipe_id}`, 404)
    if (!body.items.length) throw new ApiError('invalid', 'batch needs at least one item', 400)
    const batch_id = uid('batch')
    const run_ids: string[] = []
    for (const [i, it] of body.items.entries()) {
      const meta = { source: 'app', batch_id, batch_index: i, batch_total: body.items.length, batch_title: body.title ?? '' }
      const { run_id } = await this.startRun({ recipe_id: body.recipe_id, inputs: { ...body.inputs, ...it.inputs }, title: it.title, params: { $meta: meta }, project_id: body.project_id })
      run_ids.push(run_id)
    }
    return { batch_id, run_ids }
  }

  async presets(): Promise<ExportPreset[]> {
    await sleep(60)
    return clone([...MOCK_PRESETS, ...this.userPresets.values()])
  }

  async savePreset(p: ExportPreset): Promise<ExportPreset> {
    await sleep(120)
    if (MOCK_PRESETS.some((b) => b.id === p.id)) throw new ApiError('conflict', `'${p.id}' is a built-in preset; save a copy under another id`, 409)
    if (new Set(p.dir_order).size !== 8) throw new ApiError('invalid', 'dir_order must list each direction exactly once', 422)
    const saved = { ...clone(p), builtin: false }
    this.userPresets.set(p.id, saved)
    return clone(saved)
  }

  async deletePreset(id: string): Promise<void> {
    await sleep(80)
    if (!this.userPresets.delete(id)) throw new ApiError('not_found', `no user preset '${id}'`, 404)
  }

  async toolPaths(): Promise<ToolPaths> {
    await sleep(140)
    return { tools: clone(this.tools), settings_file: 'C:\\Users\\mock\\AppData\\Roaming\\AI Asset Studio\\data\\settings.json' }
  }

  async diagnostics(): Promise<CoreDiagnostics> {
    await this.ready
    await sleep(160)
    const data = 'C:\\Users\\mock\\AppData\\Roaming\\AI Asset Studio\\data'
    const failed = [...this.steps.values()]
      .filter((s) => s.status === 'failed')
      .sort((a, b) => (b.ended_at ?? 0) - (a.ended_at ?? 0))
      .slice(0, 20)
    return {
      generated_at: now(),
      core: { version: 'mock', pid: 4242, started_at: now() - 3600, frozen: false, python: '3.13.5', executable: 'mock\\python.exe', platform: 'Windows-11-mock' },
      data_dir: data,
      settings_file: `${data}\\settings.json`,
      tools: this.tools.map((t) => ({ key: t.key, label: t.label, path: t.path, exists: t.exists, ok: t.ok, detail: t.detail })),
      resources: clone([...this.resourcesMap.values()]),
      counts: { recipes: this.builtins.size + this.userRecipes.size, kinds: MOCK_KINDS.length },
      failed_steps: failed.map((s) => ({
        run_id: s.run_id,
        step_id: s.id,
        key: s.key,
        kind: s.kind,
        attempt: s.attempt,
        code: s.error?.code ?? null,
        message: s.error?.message ?? '',
        ended_at: s.ended_at ?? null
      })),
      logs: [{ name: 'comfyui.log', path: `${data}\\logs\\comfyui.log`, bytes: 2048, modified: now(), lines: ['[mock] ComfyUI started', '[mock] Prompt executed in 3.2 seconds'] }]
    }
  }

  private setup: { job: SetupJob; t0: number } | null = null

  async setupPlan(dirs: { tools_dir?: string; models_dir?: string } = {}): Promise<{ plan: SetupPlan; job: SetupJob | null }> {
    await sleep(220)
    const GB = 1024 ** 3
    const tools = dirs.tools_dir || 'D:\AI Asset Studio\tools'
    const plan: SetupPlan = {
      tools_dir: tools,
      models_dir: dirs.models_dir || `${tools}\models`,
      free: { tools: 412 * GB, models: 412 * GB },
      gpu: { name: 'NVIDIA GeForce RTX 5070', driver: 610.88, vram_gb: 11.9, problem: null },
      packs: [
        { id: 'base', label: 'Cơ bản 2D', detail: 'Concept 2D, texture, tileset, tách nền. Mọi quy trình đều cần.', required: true, files: 5, size: 24.2 * GB, missing_size: 24.2 * GB, installed: false },
        { id: 'edit', label: 'Biến thể + Spine', detail: 'Biến thể 2D, nhân vật Spine (tách part, vá khớp).', required: false, files: 3, size: 6.1 * GB, missing_size: 6.1 * GB, installed: false },
        { id: 'angles', label: 'Nhiều góc nhìn', detail: 'Sprite 8 hướng 2D, sprite 3D multi-view, hoạt ảnh, Spine trước + sau.', required: false, files: 5, size: 31.3 * GB, missing_size: 31.3 * GB, installed: false },
        { id: 'mesh', label: '3D', detail: 'Prop 3D, sprite 8 hướng qua mesh, hoạt ảnh.', required: false, files: 7, size: 20.3 * GB, missing_size: 0, installed: true }
      ],
      components: {
        comfyui: { installed: false, path: 'L:\AI\setup\comfyUI', version: '0.41.0', size: 12 * GB },
        blender: { installed: true, path: 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe', version: '5.2.2', size: 0.4 * GB },
        claude: { installed: false, path: 'C:\Users\mock\.local\bin\claude.exe' }
      }
    }
    return { plan, job: await this.setupJob() }
  }

  async setupJob(): Promise<SetupJob | null> {
    if (!this.setup) return null
    const { job, t0 } = this.setup
    if (job.state !== 'running') return clone(job)
    const elapsed = (Date.now() - t0) / 1000
    let left = elapsed
    for (const it of job.items) {
      const need = it.total ? it.total / (900 * 1024 ** 2) : 3
      if (left <= 0) it.state = 'pending'
      else if (left >= need) Object.assign(it, { state: 'done', done: it.total, detail: '' })
      else Object.assign(it, { state: 'running', done: Math.round((it.total * left) / need), detail: it.total ? 'Đang tải' : 'Đang cài' })
      left -= need
    }
    job.bytes_done = job.items.reduce((a, i) => a + i.done, 0)
    job.speed = 900 * 1024 ** 2
    if (job.items.every((i) => i.state === 'done')) Object.assign(job, { state: 'done', finished: now(), speed: 0 })
    return clone(job)
  }

  async startSetup(body: SetupStartBody): Promise<SetupJob> {
    await sleep(160)
    const GB = 1024 ** 3
    const items = [
      { id: 'comfyui', label: 'ComfyUI + Python + PyTorch', state: 'pending' as const, detail: '', done: 0, total: 0 },
      { id: 'pack:base', label: 'Gói Cơ bản 2D (5 file)', state: 'pending' as const, detail: '', done: 0, total: 24.2 * GB },
      { id: 'pack:edit', label: 'Gói Biến thể + Spine (3 file)', state: 'pending' as const, detail: '', done: 0, total: 6.1 * GB },
      ...(body.claude ? [{ id: 'claude', label: 'Claude Code', state: 'pending' as const, detail: '', done: 0, total: 0 }] : []),
      { id: 'finish', label: 'Lưu đường dẫn', state: 'pending' as const, detail: '', done: 0, total: 0 }
    ]
    const job: SetupJob = { state: 'running', error: null, started: now(), finished: null, items, bytes_done: 0, bytes_total: 30.3 * GB, speed: 0, tools_dir: body.tools_dir, models_dir: body.models_dir }
    this.setup = { job, t0: Date.now() }
    return clone(job)
  }

  async cancelSetup(): Promise<SetupJob | null> {
    if (this.setup?.job.state === 'running') {
      this.setup.job.state = 'cancelled'
      for (const i of this.setup.job.items) if (i.state === 'pending' || i.state === 'running') i.state = 'cancelled'
    }
    return this.setup ? clone(this.setup.job) : null
  }

  async claudeLogin(): Promise<void> {
    await sleep(80)
  }

  async saveToolPaths(paths: Partial<Record<ToolKey, string>>): Promise<ToolPaths> {
    await sleep(200)
    for (const [key, raw] of Object.entries(paths)) {
      const t = this.tools.find((x) => x.key === key)
      if (!t) throw new ApiError('invalid', `unknown tool keys: ${key}`, 400)
      const path = (raw ?? '').trim()
      if (!path) throw new ApiError('invalid', `empty path: ${key}`, 400)
      // Mock "exists": any absolute drive path counts, so the not-found state stays reachable by typing junk.
      const exists = /^[a-z]:[\\/]/i.test(path)
      Object.assign(t, { path, exists, ok: exists, detail: exists ? t.detail : t.kind === 'dir' ? 'Không tìm thấy thư mục' : 'Không tìm thấy file' })
    }
    return this.toolPaths()
  }

  async startRun(body: StartRunBody): Promise<{ run_id: string }> {
    const recipe = this.recipe(body.recipe_id)
    if (!recipe) throw new ApiError('unknown_recipe', `Không có quy trình ${body.recipe_id}`, 400)
    const title = body.title || String(body.inputs.prompt ?? recipe.info.name).slice(0, 60)
    const { inputs, applied, projectId } = this.applyProject(body.project_id, recipe, body.inputs)
    let params = body.params ?? {}
    if (projectId !== 'default') params = { ...params, $meta: { ...((params.$meta as object | undefined) ?? {}), project_id: projectId, project_applied: applied } }
    const run = this.newRun(recipe, inputs, title, now(), params)
    run.project_id = projectId
    const steps = this.createSteps(run, recipe)
    this.emit('run.created', run, run.id)
    for (const s of steps) this.emit('step.updated', s, run.id, s.id)
    window.setTimeout(() => this.advance(run.id), 150)
    return { run_id: run.id }
  }

  async listRuns(q: ListRunsQuery = {}): Promise<RunRow[]> {
    await this.ready
    let rows = [...this.runs.values()].sort((a, b) => b.created_at - a.created_at)
    if (q.status) rows = rows.filter((r) => r.status === q.status)
    if (q.project_id) rows = rows.filter((r) => r.project_id === q.project_id)
    if (q.before) rows = rows.filter((r) => r.created_at < q.before!)
    const batchOf = (r: RunRow): unknown => (r.params?.$meta as { batch_id?: string } | undefined)?.batch_id
    if (q.batch_id) rows = rows.filter((r) => batchOf(r) === q.batch_id)
    else if (q.batched) rows = rows.filter((r) => batchOf(r) != null)
    return clone(rows.slice(0, q.limit ?? 50).map((r) => ({ ...r, preview_hash: this.previewHash(r.id) })))
  }

  // Same rule as the core: first image output of the latest step that produced one, sprite sheets skipped.
  private previewHash(runId: string): string | null {
    const steps = this.stepsOf(runId).sort((a, b) => b.order - a.order)
    for (const s of steps) {
      for (const [port, blobs] of Object.entries(s.outputs)) {
        if (port === 'sheet') continue
        const img = blobs.find((b) => b.mime.startsWith('image/'))
        if (img) return img.hash
      }
    }
    return null
  }

  async getRun(id: string): Promise<RunDetail> {
    await this.ready
    const run = this.runs.get(id)
    if (!run) throw new ApiError('not_found', 'Không tìm thấy lượt chạy', 404)
    return clone({ run, steps: this.stepsOf(id) })
  }

  async cancelRun(id: string): Promise<void> {
    const run = this.runs.get(id)
    if (!run) throw new ApiError('not_found', 'Không tìm thấy lượt chạy', 404)
    this.cancelled.add(id)
    for (const s of this.stepsOf(id)) {
      if (s.status === 'pending') this.patchStep(s, { status: 'skipped' })
      else if (['ready', 'waiting_resource', 'awaiting_human'].includes(s.status)) this.patchStep(s, { status: 'cancelled', ended_at: now() })
    }
    window.setTimeout(() => this.patchRun(run, { status: 'cancelled', ended_at: now() }), 300)
  }

  async retryStep(stepId: string): Promise<void> {
    const s = this.steps.get(stepId)
    if (!s) throw new ApiError('not_found', 'Không có bước này', 404)
    const run = this.runs.get(s.run_id)!
    const steps = this.stepsOf(run.id)
    const downstream = new Set<string>([s.key])
    for (const x of steps) if (x.deps.some((d) => downstream.has(d))) downstream.add(x.key)
    const regen = ['succeeded', 'cached'].includes(s.status) &&
      steps.some((x) => downstream.has(x.key) && x.key !== s.key && x.status === 'awaiting_human')
    if (!regen && !['failed', 'cancelled'].includes(s.status)) {
      throw new ApiError('bad_state', 'Chỉ thử lại được bước lỗi hoặc đã huỷ', 409)
    }
    this.cancelled.delete(run.id)
    for (const x of steps) {
      if (x.id === s.id) this.patchStep(x, { status: 'pending', attempt: x.attempt + 1, error: null, progress: null, outputs: {}, started_at: null, ended_at: null })
      else if (downstream.has(x.key) || x.status === 'skipped') this.patchStep(x, { status: 'pending', error: null, progress: null, outputs: {}, started_at: null, ended_at: null })
    }
    this.patchRun(run, { status: 'running', error: null, ended_at: null })
    window.setTimeout(() => this.advance(run.id), 120)
  }

  async gates(): Promise<StepRow[]> {
    await this.ready
    return clone([...this.steps.values()].filter((s) => s.status === 'awaiting_human'))
  }

  async resolveGate(stepId: string, res: GateResolution): Promise<void> {
    const s = this.steps.get(stepId)
    if (!s || s.status !== 'awaiting_human') throw new ApiError('bad_state', 'Cổng duyệt không còn chờ', 409)
    const imgs = s.inputs.images ?? []
    if (s.kind === 'gate.pick') {
      const picked = imgs.filter((b) => res.picked?.includes(b.hash))
      if (!picked.length) throw new ApiError('invalid', 'Chọn ít nhất 1 ảnh', 400)
      this.patchStep(s, { status: 'succeeded', outputs: { images: picked }, ended_at: now() })
    } else if (res.approved) {
      this.patchStep(s, { status: 'succeeded', outputs: s.inputs, ended_at: now() })
    } else {
      this.patchStep(s, { status: 'failed', error: { code: 'rejected', message: 'Bị từ chối ở bước duyệt' }, ended_at: now() })
    }
    this.advance(s.run_id)
  }

  async versions(q: VersionsQuery = {}): Promise<VersionRow[]> {
    await this.ready
    let rows = [...this.versionMap.values()].sort((a, b) => b.created_at - a.created_at)
    if (q.run_id) rows = rows.filter((v) => v.run_id === q.run_id)
    if (q.project_id) rows = rows.filter((v) => v.project_id === q.project_id)
    if (q.status) rows = rows.filter((v) => v.status === q.status)
    return clone(rows)
  }

  async setVersionStatus(id: string, action: 'approve' | 'reject'): Promise<VersionRow> {
    const v = this.versionMap.get(id)
    if (!v) throw new ApiError('not_found', 'Không tìm thấy phiên bản', 404)
    v.status = action === 'approve' ? 'approved' : 'rejected'
    this.emit('version.updated', v, v.run_id)
    return clone(v)
  }

  // ---- projects ----

  private project(id: string): ProjectRow {
    const p = this.projectMap.get(id)
    if (!p) throw new ApiError('not_found', `unknown project: ${id}`, 404)
    return p
  }

  private summaryOf(p: ProjectRow): ProjectSummary {
    const runs = [...this.runs.values()].filter((r) => r.project_id === p.id).sort((a, b) => b.created_at - a.created_at)
    const vers = [...this.versionMap.values()].filter((v) => v.project_id === p.id)
    const count = <K extends string>(keys: K[]): Partial<Record<K, number>> => {
      const out: Partial<Record<K, number>> = {}
      for (const k of keys) out[k] = (out[k] ?? 0) + 1
      return out
    }
    const last = Math.max(p.updated_at, ...runs.map((r) => Math.max(r.created_at, r.ended_at ?? 0)))
    const cover = vers
      .filter((v) => v.status !== 'rejected' && v.blob.mime.startsWith('image/'))
      .sort((a, b) => Number(b.status === 'approved') - Number(a.status === 'approved') || b.created_at - a.created_at)[0]
    return {
      project_id: p.id,
      runs: count(runs.map((r) => r.status)),
      runs_total: runs.length,
      versions: count(vers.map((v) => v.status)),
      approved_versions: vers.filter((v) => v.status === 'approved').length,
      last_activity: last,
      cover_hash: cover?.blob.hash ?? (runs[0] ? this.previewHash(runs[0].id) : null)
    }
  }

  async projects(q: { archived?: boolean } = {}): Promise<ProjectWithSummary[]> {
    await this.ready
    await sleep(90)
    return clone([...this.projectMap.values()].filter((p) => p.archived === !!q.archived).map((p) => ({ ...p, summary: this.summaryOf(p) })))
  }

  async getProject(id: string): Promise<ProjectRow> {
    await this.ready
    return clone(this.project(id))
  }

  async projectSummary(id: string): Promise<ProjectSummary> {
    await this.ready
    return clone(this.summaryOf(this.project(id)))
  }

  async createProject(body: ProjectCreateBody): Promise<ProjectRow> {
    await sleep(160)
    const name = body.name.trim()
    if (!name) throw new ApiError('invalid', 'name must not be empty', 400)
    const t = now()
    const p: ProjectRow = { id: uid('prj'), name, description: body.description ?? '', style: normalizeStyle(body.style), created_at: t, updated_at: t, archived: false }
    this.projectMap.set(p.id, p)
    this.emit('project.updated', p)
    return clone(p)
  }

  async patchProject(id: string, body: ProjectPatchBody): Promise<ProjectRow> {
    await sleep(140)
    const p = this.project(id)
    if (body.name !== undefined && !body.name.trim()) throw new ApiError('invalid', 'name must not be empty', 400)
    const preset = body.style?.preset
    if (preset && ![...MOCK_PRESETS, ...this.userPresets.values()].some((x) => x.id === preset)) {
      throw new ApiError('invalid', `style: unknown export preset ${preset}`, 400)
    }
    if (body.name !== undefined) p.name = body.name.trim()
    if (body.description !== undefined) p.description = body.description
    if (body.style) p.style = normalizeStyle(body.style)
    if (body.archived !== undefined) p.archived = body.archived
    p.updated_at = now()
    this.emit('project.updated', p)
    return clone(p)
  }

  async archiveProject(id: string): Promise<ProjectRow> {
    return this.patchProject(id, { archived: true })
  }

  private manifests = new Map<string, ManifestRow[]>()

  private manifestView(projectId: string, extra: Partial<ManifestView> = {}): ManifestView {
    const rows = (this.manifests.get(projectId) ?? []).map((r) => {
      const run = r.run_id ? this.runs.get(r.run_id) : undefined
      const approved = !!run && [...this.versionMap.values()].some((v) => v.run_id === run.id && v.status === 'approved')
      const status: ManifestStatus = !run || run.status === 'cancelled' ? 'todo' : run.status === 'running' ? 'running'
        : run.status === 'failed' || run.status === 'interrupted' ? 'failed' : approved ? 'approved' : 'review'
      return { ...r, status, run_status: run?.status ?? null }
    })
    const counts: ManifestView['counts'] = {}
    for (const r of rows) counts[r.status] = (counts[r.status] ?? 0) + 1
    return clone({ project_id: projectId, rows, counts, updated_at: now(), ...extra })
  }

  async getManifest(projectId: string): Promise<ManifestView> {
    await this.ready
    return this.manifestView(projectId)
  }

  async putManifest(projectId: string, rows: ManifestRow[]): Promise<ManifestView> {
    await sleep(80)
    this.manifests.set(projectId, clone(rows).map((r) => ({ ...r, id: r.id || uid('row') })))
    return this.manifestView(projectId)
  }

  async manifestFromGdd(_projectId: string, text: string, limit = 30): Promise<GddProposal> {
    await sleep(1200)
    const words = text.split(/\s+/).filter((w) => w.length > 4).slice(0, Math.min(limit, 5))
    const recipe = [...this.builtins.keys()][0] ?? 'concept-2d'
    return { rows: words.map((w) => ({ id: uid('row'), name: w, recipe_id: recipe, inputs: { prompt: w }, notes: 'mock', run_id: null })), dropped: [] }
  }

  async runManifest(projectId: string, rowIds?: string[]): Promise<ManifestView> {
    const view = this.manifestView(projectId)
    const chosen = view.rows.filter((r) => (rowIds ? rowIds.includes(r.id) : r.status === 'todo' || r.status === 'failed'))
    if (!chosen.length) throw new ApiError('invalid', 'no rows to run', 400)
    const batch_id = uid('batch')
    const run_ids: string[] = []
    const rows = this.manifests.get(projectId) ?? []
    for (const [i, r] of chosen.entries()) {
      const meta = { source: 'manifest', batch_id, batch_index: i, batch_total: chosen.length, batch_title: 'Danh sách asset', manifest_row: r.id }
      const { run_id } = await this.startRun({ recipe_id: r.recipe_id, inputs: r.inputs, title: r.name, params: { $meta: meta }, project_id: projectId })
      run_ids.push(run_id)
      const stored = rows.find((x) => x.id === r.id)
      if (stored) stored.run_id = run_id
    }
    return this.manifestView(projectId, { batch_id, run_ids })
  }

  async exportManifest(projectId: string, _format: ManifestFormat): Promise<string> {
    return JSON.stringify(this.manifests.get(projectId) ?? [], null, 1)
  }

  async importManifest(_projectId: string, _format: ManifestFormat, _text: string, _append: boolean): Promise<ManifestView> {
    throw new ApiError('invalid', 'Bản mock không đọc CSV/YAML', 400)
  }

  private projectQa = new Map<string, ProjectQaReport>()

  async getProjectQa(projectId: string): Promise<ProjectQaReport> {
    await this.ready
    return clone(this.projectQa.get(projectId) ?? { project_id: projectId, created_at: null, assets: [], summary: null })
  }

  async runProjectQa(projectId: string, body: { only_approved?: boolean; vision?: boolean } = {}): Promise<ProjectQaReport> {
    await sleep(body.vision ? 1500 : 500)
    const list = [...this.versionMap.values()].filter(
      (v) => v.project_id === projectId && v.blob.mime.startsWith('image/') && v.status !== 'rejected' && (!body.only_approved || v.status === 'approved')
    )
    // Deterministic pseudo-scores from the hash so the panel shows a mix of grades.
    const assets: ProjectQaAsset[] = list.slice(0, 120).map((v): ProjectQaAsset => {
      const score = 45 + (parseInt(v.blob.hash.slice(0, 4), 16) % 56)
      const issues: ProjectQaAsset['issues'] =
        score < 80 ? [{ key: 'palette', label: 'Bảng màu', severity: score < 60 ? 'bad' : 'warn', message: 'Màu lệch bảng màu chung (gấp 1.9× mức chung)', fix: 'recolor it using only this color palette: #2d3a4a, #6aa84f' }] : []
      return {
        version_id: v.id, run_id: v.run_id, asset_name: v.asset_name, status: v.status, blob: v.blob, colors: ['#2d3a4a', '#6aa84f', '#e0c068'],
        score, grade: score >= 80 ? 'ok' : score >= 60 ? 'warn' : 'bad', parts: { palette: { score } }, issues, note: '',
        fix: issues.length ? { recipe_id: 'variants-2d', title: `Sửa QA · ${v.asset_name}`, inputs: { image: v.blob.hash, instructions: issues[0]!.fix } } : null,
        vision_note: body.vision && score < 60 ? 'Tông màu lạc so với cả bộ' : undefined
      }
    }).sort((a, b) => (a.score ?? 0) - (b.score ?? 0))
    const scored = assets.map((a) => a.score ?? 0)
    const report: ProjectQaReport = {
      project_id: projectId, created_at: now(), assets,
      summary: { total: assets.length, scored: scored.length, score: scored.length ? Math.round(scored.reduce((x, y) => x + y, 0) / scored.length) : null,
        flagged: assets.filter((a) => a.issues.length).length,
        counts: { ok: assets.filter((a) => a.grade === 'ok').length, warn: assets.filter((a) => a.grade === 'warn').length, bad: assets.filter((a) => a.grade === 'bad').length, skip: 0 } },
      reference: { palette: ['#2d3a4a', '#6aa84f', '#e0c068', '#c2c3c7'], palette_source: 'Màu chung của các asset trong dự án', fill_median: 0.86, outlined: true, outline_median: 3.2, light: 135, light_label: 'trên-trái' },
      vision: body.vision ? { summary: 'Bộ asset khá đồng bộ; vài asset lệch tông màu cần chỉnh trước.', cost_usd: 0.04, model: 'claude-opus-5-5' } : undefined
    }
    this.projectQa.set(projectId, report)
    return clone(report)
  }

  async fixProjectQa(projectId: string, versionId: string, instructions?: string): Promise<ProjectQaFixResult> {
    const v = this.versionMap.get(versionId)
    if (!v) throw new ApiError('not_found', `unknown version: ${versionId}`, 404)
    const entry = this.projectQa.get(projectId)?.assets.find((a) => a.version_id === versionId)
    const text = instructions?.trim() || String(entry?.fix?.inputs.instructions ?? '')
    if (!text) throw new ApiError('invalid', 'no issues to fix for this asset; pass instructions', 400)
    const recipe_id = this.builtins.has('variants-2d') ? 'variants-2d' : ([...this.builtins.keys()][0] ?? 'variants-2d')
    const title = `Sửa QA · ${v.asset_name}`
    const inputs = { image: v.blob.hash, instructions: text }
    const { run_id } = await this.startRun({ recipe_id, inputs, title, params: { $meta: { source: 'project_qa', qa_version: versionId } }, project_id: projectId })
    return { run_id, recipe_id, inputs, title }
  }

  async statsOverview(q: StatsQuery = {}): Promise<StatsOverview> {
    await this.ready
    await sleep(120)
    return clone(mockStatsOverview([...this.runs.values()], [...this.steps.values()], q))
  }

  private mockQa(v: VersionRow): QaResult | null {
    if (!v.blob.mime.startsWith('image/')) return null
    // Core skips images without transparency; mock blobs are not decoded, so the name stands in for the alpha check.
    if (!/sheet|dir_|walk_|hero|_fit|_norm|_fx|layers|parts/i.test(v.blob.name ?? '')) {
      return { version: 1, score: null, skipped: 'opaque', note: 'Ảnh không có nền trong suốt — không chấm' }
    }
    const seed = parseInt(v.blob.hash.slice(0, 6), 16) || 0
    const part = (key: QaPart['key'], label: string, weight: number, k: number, detail: string): QaPart => ({ key, label, weight, score: 100 - ((seed >> k) % 45), detail })
    const parts = [
      part('empty', 'Khung rỗng', 20, 1, 'Không có khung rỗng'),
      part('alpha', 'Độ sạch alpha', 25, 3, 'Pixel lẻ 0.4% · viền mờ 1.2%'),
      part('framing', 'Khung hình', 25, 5, 'Không chạm mép, lề ổn'),
      part('consistency', 'Nhất quán các hướng', 20, 7, '8 hướng · lệch cao 3% · lệch chân 0.5% · lệch tâm 1.0%')
    ]
    const score = Math.round(parts.reduce((a, p) => a + p.score * p.weight, 0) / 90)
    return { version: 1, score, parts, issues: score < 70 ? ['Có pixel lẻ / mảnh rời quanh sprite'] : [], cells: 8 }
  }

  async recomputeQa(body: { run_id?: string; project_id?: string; missing_only?: boolean; limit?: number } = {}): Promise<QaRecomputeResult> {
    await this.ready
    let updated = 0
    let skipped = 0
    const rows = [...this.versionMap.values()].filter((v) => (!body.run_id || v.run_id === body.run_id) && (!body.project_id || v.project_id === body.project_id))
    for (const v of rows) {
      if ((body.missing_only ?? true) && v.qa) {
        skipped += 1
        continue
      }
      v.qa = this.mockQa(v)
      if (v.qa) updated += 1
      else skipped += 1
      this.emit('version.updated', v, v.run_id)
    }
    return { updated, skipped, total: rows.length }
  }

  async recomputeVersionQa(id: string): Promise<VersionRow> {
    const v = this.versionMap.get(id)
    if (!v) throw new ApiError('not_found', `unknown version: ${id}`, 404)
    v.qa = this.mockQa(v)
    this.emit('version.updated', v, v.run_id)
    return clone(v)
  }

  async enginePreviewEngines(): Promise<PreviewEngines> {
    const off = { available: false, path: '', reason: 'Bản xem thử không chạy được engine thật' }
    return { engines: { godot: { ...off }, unity: { ...off } } }
  }

  async startEnginePreview(): Promise<EnginePreviewJob> {
    throw new ApiError('engine_unavailable', 'Bản xem thử không chạy được engine thật', 409)
  }

  async enginePreviewJob(jobId: string): Promise<EnginePreviewJob> {
    throw new ApiError('not_found', `unknown engine preview: ${jobId}`, 404)
  }

  async cancelEnginePreview(jobId: string): Promise<EnginePreviewJob> {
    return this.enginePreviewJob(jobId)
  }
}
