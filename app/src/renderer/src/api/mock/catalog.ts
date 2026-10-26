import type { Binding, ExportPreset, KindInfo, Lane, ParamSpec, RecipeDoc, RecipeIssue, RecipeStepDoc, ToolPath } from '../types'

const RESAMPLE = ['lanczos', 'bicubic', 'bilinear', 'box', 'nearest']
const int = (d: number | undefined, label: string, extra: Partial<ParamSpec> = {}): ParamSpec => ({ type: 'int', default: d, label, ...extra })
const float = (d: number, label: string, extra: Partial<ParamSpec> = {}): ParamSpec => ({ type: 'float', default: d, label, ...extra })
const bool = (d: boolean, label: string): ParamSpec => ({ type: 'bool', default: d, label })
const text = (label: string, extra: Partial<ParamSpec> = {}): ParamSpec => ({ type: 'text', label, ...extra })
const choice = (options: string[], d: string, label: string): ParamSpec => ({ type: 'enum', options, default: d, label })
const COMFY_OPTS: Record<string, ParamSpec> = {
  _free_before: { ...bool(false, 'Giải phóng VRAM trước khi chạy'), advanced: true },
  _timeout_s: { ...int(1800, 'Giới hạn thời gian (giây)'), advanced: true },
  _auto_retry: { ...int(1, 'Tự thử lại khi lỗi GPU', { min: 0, max: 3 }), advanced: true }
}

function k(kind: string, lane: Lane, label: string, inp: KindInfo['in'], out: KindInfo['out'], params: Record<string, ParamSpec> = {}, free = false): KindInfo {
  return { kind, lane, label, in: inp, out, params, free_params: free }
}

export const MOCK_KINDS: KindInfo[] = [
  k('gate.pick', 'none', 'Chọn ảnh', { images: 'image[]' }, { images: 'image[]' }, { k: int(1, 'Số ảnh cần chọn (0 = ít nhất 1)', { min: 0 }) }),
  k('gate.approve', 'none', 'Duyệt kết quả', { '*': 'any' }, { '*': 'any' }),
  k('op.trim_pad_resize', 'cpu', 'Cắt viền & căn cỡ', { images: 'image[]' }, { images: 'image[]' }, {
    size: int(512, 'Cỡ (px)', { min: 8, max: 4096 }),
    pad: float(0.06, 'Lề', { min: 0, max: 0.5 }),
    square: bool(true, 'Vuông'),
    resample: choice(RESAMPLE, 'lanczos', 'Lấy mẫu')
  }),
  k('op.normalize', 'cpu', 'Chuẩn hoá khung', { images: 'image[]' }, { images: 'image[]' }, {
    size: int(512, 'Cỡ (px)', { min: 8, max: 4096 }),
    anchor: choice(['bottom', 'center'], 'bottom', 'Neo')
  }),
  k('op.mirror_dirs', 'cpu', 'Lật thành 8 hướng', { images: 'image[]' }, { images: 'image[]' }, { count: int(8, 'Số hướng', { options: [4, 8, 16] }) }),
  k('op.downscale', 'cpu', 'Thu nhỏ', { images: 'image[]' }, { images: 'image[]' }, { factor: float(2, 'Hệ số', { min: 1, max: 16 }), resample: choice(RESAMPLE, 'box', 'Lấy mẫu') }),
  k('op.pixelate', 'cpu', 'Pixel art', { images: 'image[]' }, { images: 'image[]' }, { size: int(64, 'Cỡ (px)'), palette_colors: int(undefined, 'Số màu', { min: 2, max: 256 }), outline: text('Màu viền (#rrggbb)') }),
  k('op.sheet', 'cpu', 'Ghép sprite sheet', { images: 'image[]', pivot: 'json' }, { sheet: 'image', meta: 'json' }, {
    preset: { type: 'preset', label: 'Preset xuất' },
    cols: int(undefined, 'Số cột', { min: 1, max: 64 }),
    cell: int(undefined, 'Cỡ ô (px)', { min: 8, max: 4096 }),
    padding: int(0, 'Khoảng cách', { min: 0, max: 64 }),
    pivot: choice(['', 'bottom'], '', 'Pivot'),
    order: { type: 'list', label: 'Góc của từng khung' },
    asset: text('Tên asset (đặt tên khung)')
  }),
  k('op.glb_validate', 'cpu', 'Kiểm tra GLB', { mesh: 'mesh' }, { meshes: 'mesh' }, { require_textures: bool(true, 'Bắt buộc texture') }),
  k('op.parts', 'cpu', 'Tách part (rig 2D)', { image: 'image', masks: 'image[]' }, { meta: 'json', labels: 'image', preview: 'image' }, {
    roles: { type: 'list', default: ['head', 'torso', 'arm', 'hand', 'leg', 'cape'], label: 'Vai trò của từng mask' }
  }),
  k('op.layers', 'cpu', 'Tách layer & khớp', { image: 'image', labels: 'image', parts: 'json', bones: 'json' }, { layers: 'image[]', meta: 'json', preview: 'image', holes: 'image[]' }, {
    rig: choice(['claude', 'auto'], 'claude', 'Đặt khớp'),
    fill: bool(true, 'Tạo vùng cần vá ở khớp'),
    overlap: float(0.6, 'Độ chồng ở khớp', { min: 0.2, max: 1.5 })
  }),
  k('op.spine_export', 'cpu', 'Xuất Spine 4.2', { layers: 'image[]', meta: 'json', holes: 'image[]', fills: 'image[]', image: 'image' }, { skeleton: 'json', atlas: 'text', texture: 'image', preview: 'image', html: 'text', report: 'json', frames: 'image' }, {
    name: text('Tên skeleton', { default: 'character' }),
    animations: bool(true, 'Thêm anim idle/relax'),
    padding: int(2, 'Khoảng cách atlas', { min: 0, max: 32 })
  }),
  k('codex.image', 'cloud', 'Sinh ảnh (Codex)', { refs: 'image[]' }, { images: 'image[]' }, {
    prompt: text('Prompt', { multiline: true }),
    count: int(1, 'Số ảnh', { min: 1, max: 4 }),
    transparent: bool(false, 'Nền trong suốt')
  }),
  k('claude.brief', 'cloud', 'Claude viết brief', {}, { json: 'json', text: 'text' }, { idea: text('Ý tưởng', { multiline: true }), model: choice(['opus', 'sonnet', 'haiku'], 'opus', 'Model') }),
  k('blender.cleanup', 'blender', 'Dọn mesh (Blender)', { mesh: 'mesh' }, { meshes: 'mesh' }, {
    target_faces: int(50000, 'Số mặt đích', { min: 100 }),
    origin: choice(['feet', 'center', 'keep'], 'feet', 'Gốc toạ độ')
  }),
  k('blender.render_dirs', 'blender', 'Render các hướng', { mesh: 'mesh' }, { images: 'image[]', meta: 'json' }, {
    count: int(8, 'Số hướng', { options: [4, 8, 16] }),
    elevation: float(30, 'Góc nghiêng', { min: 0, max: 90 }),
    res: int(256, 'Cỡ khung', { options: [64, 128, 256, 512, 1024] }),
    mirror: bool(false, 'Lật gương (5 hướng → 8)'),
    engine: choice(['BLENDER_EEVEE', 'CYCLES', 'BLENDER_WORKBENCH'], 'BLENDER_EEVEE', 'Engine')
  }),
  k('comfy.t2i_zimage_turbo', 'gpu', 'Sinh ảnh (Z-Image)', {}, { images: 'image[]' }, { '57:27.text': text('CLIP Text Encode (Prompt)', { multiline: true, node: true }), ...COMFY_OPTS }, true),
  k('comfy.remove_bg_birefnet', 'gpu', 'Tách nền (BiRefNet)', { image: 'image' }, { images: 'image[]' }, { ...COMFY_OPTS }, true),
  k('comfy.i23d_hunyuan', 'gpu', 'Dựng mesh (Hunyuan3D)', { image: 'image' }, { meshes: 'mesh' }, { ...COMFY_OPTS }, true),
  k('comfy.img2mesh_trellis2_textured', 'gpu', 'Dựng mesh (TRELLIS)', { image: 'image' }, { meshes: 'mesh' }, { '186.target_face_count': int(30000, 'Số mặt'), ...COMFY_OPTS }, true),
  k('comfy.multi_angles_qwen2511', 'gpu', 'Nhiều góc (Qwen)', { image: 'image' }, { images: 'image[]' }, { ...COMFY_OPTS }, true)
]

export const MOCK_PRESETS: ExportPreset[] = [
  { id: 'studio', name: 'Studio (mặc định)', description: 'Theo góc xoay 0°→315°, một hàng, tên theo góc', dir_order: ['S', 'SW', 'W', 'NW', 'N', 'NE', 'E', 'SE'], dir_labels: {}, file_pattern: '{asset}_{angle:03d}', layout: 'row', cols: 4, padding: 0, pivot_origin: 'top_left', builtin: true },
  { id: 'unity', name: 'Unity', description: 'E, NE, N… ngược chiều kim đồng hồ (Atan2); pivot gốc dưới-trái', dir_order: ['E', 'NE', 'N', 'NW', 'W', 'SW', 'S', 'SE'], dir_labels: {}, file_pattern: '{asset}_{dir}', layout: 'row', cols: 4, padding: 0, pivot_origin: 'bottom_left', builtin: true },
  {
    id: 'godot',
    name: 'Godot',
    description: 'right, down_right, down… theo chiều kim đồng hồ (Vector2.angle)',
    dir_order: ['E', 'SE', 'S', 'SW', 'W', 'NW', 'N', 'NE'],
    dir_labels: { E: 'right', SE: 'down_right', S: 'down', SW: 'down_left', W: 'left', NW: 'up_left', N: 'up', NE: 'up_right' },
    file_pattern: '{asset}_{dir}',
    layout: 'row',
    cols: 4,
    padding: 0,
    pivot_origin: 'top_left',
    builtin: true
  },
  { id: 'pixel', name: 'Pixel art', description: 'Như Studio, lọc Point để giữ nét pixel', dir_order: ['S', 'SW', 'W', 'NW', 'N', 'NE', 'E', 'SE'], dir_labels: {}, file_pattern: '{asset}_{angle:03d}', layout: 'row', cols: 8, padding: 0, pivot_origin: 'top_left', builtin: true }
]

export function bindingSources(b: Binding): string[] {
  return (Array.isArray(b) ? b : [b]).map((x) => x.split('.')[0] ?? '')
}

const STEP_REF = /\$\{steps\.([A-Za-z_][\w-]*)\./g

export function stepRefs(params: unknown): string[] {
  const out: string[] = []
  const walk = (v: unknown): void => {
    if (typeof v === 'string') for (const m of v.matchAll(STEP_REF)) out.push(m[1]!)
    else if (Array.isArray(v)) v.forEach(walk)
    else if (v && typeof v === 'object') Object.values(v).forEach(walk)
  }
  walk(params)
  return out
}

export function stepDeps(s: RecipeStepDoc): string[] {
  const deps = Object.values(s.in ?? {}).flatMap(bindingSources).filter((x) => x !== 'inputs')
  return [...new Set([...deps, ...stepRefs(s.params)])]
}

// Mirrors the core's structural checks closely enough for the editor to be exercised without a core.
export function mockValidate(doc: RecipeDoc, kinds: KindInfo[]): RecipeIssue[] {
  const issues: RecipeIssue[] = []
  const err = (message: string, step: string | null = null, field: string | null = null): void => void issues.push({ level: 'error', step, field, message })
  if (!/^[A-Za-z_][\w-]*$/.test(doc.id ?? '')) err('missing/invalid id', null, 'id')
  if (!doc.steps?.length) err('steps must be a non-empty list')
  const ids = new Set<string>()
  for (const s of doc.steps ?? []) {
    if (!/^[A-Za-z_][\w-]*$/.test(s.id) || s.id === 'inputs') err(`step has invalid id '${s.id}'`, s.id, 'id')
    if (ids.has(s.id)) err(`duplicate step id '${s.id}'`, s.id, 'id')
    ids.add(s.id)
    if (!kinds.some((x) => x.kind === s.kind)) err(`unknown step kind '${s.kind}'`, s.id, 'kind')
  }
  for (const s of doc.steps ?? []) {
    for (const [name, b] of Object.entries(s.in ?? {})) {
      for (const part of Array.isArray(b) ? b : [b]) {
        const [src, out] = part.split('.')
        if (!src || !out) err(`in.${name}: bad binding '${part}'`, s.id, `in.${name}`)
        else if (src === 'inputs') {
          const t = doc.inputs?.[out]?.type
          if (t !== 'image' && t !== 'mesh') err(`in.${name} must bind an image/mesh input, got ${part}`, s.id, `in.${name}`)
        } else if (!ids.has(src)) err(`in.${name} references unknown step '${src}'`, s.id, `in.${name}`)
      }
    }
    if (s.map && !(s.map in (s.in ?? {}))) err(`map '${s.map}' is not one of its inputs`, s.id, 'map')
    if (s.map_param && !(s.map_param in (s.params ?? {}))) err(`map_param '${s.map_param}' is not one of its params`, s.id, 'map_param')
  }
  const seen = new Set<string>()
  const visiting = new Set<string>()
  const byId = new Map((doc.steps ?? []).map((s) => [s.id, s]))
  const cyclic = (id: string): boolean => {
    if (visiting.has(id)) return true
    if (seen.has(id)) return false
    visiting.add(id)
    const s = byId.get(id)
    const hit = !!s && stepDeps(s).some((d) => byId.has(d) && cyclic(d))
    visiting.delete(id)
    seen.add(id)
    return hit
  }
  for (const s of doc.steps ?? []) if (cyclic(s.id)) {
    err('dependency cycle', s.id)
    break
  }
  return issues
}

export const MOCK_TOOLS: ToolPath[] = [
  { key: 'comfy_dir', label: 'Thư mục ComfyUI', kind: 'dir', path: 'L:\\AI\\setup\\comfyUI', exists: true, ok: true, version: 'ComfyUI 0.41.0', detail: null },
  { key: 'comfy_python', label: 'Python của ComfyUI', kind: 'file', path: 'L:\\AI\\setup\\comfyUI\\.venv\\Scripts\\python.exe', exists: true, ok: true, version: 'Python 3.12.10', detail: null },
  { key: 'comfy_models_dir', label: 'Thư mục model', kind: 'dir', path: 'F:\\work\\AI\\comfyUI_models', exists: true, ok: true, version: null, detail: '14 thư mục con' },
  { key: 'comfy_workflows_dir', label: 'Thư mục workflow', kind: 'dir', path: 'L:\\AI\\setup\\workflows', exists: true, ok: true, version: null, detail: '9 workflow' },
  { key: 'blender_exe', label: 'Blender', kind: 'file', path: 'C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe', exists: true, ok: true, version: 'Blender 5.2.0', detail: null },
  { key: 'codex_exe', label: 'Codex CLI', kind: 'file', path: 'C:\\Users\\mock\\AppData\\Roaming\\npm\\codex.exe', exists: false, ok: false, version: null, detail: 'Không tìm thấy file' }
]
