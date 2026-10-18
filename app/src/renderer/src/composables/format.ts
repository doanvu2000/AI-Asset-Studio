import type { Lane, RunStatus, StepStatus } from '@/api/types'

export function clockDuration(sec: number): string {
  const s = Math.max(0, Math.floor(sec))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = String(s % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}

export function humanDuration(sec: number): string {
  if (sec < 1) return `${Math.round(sec * 1000)} ms`
  if (sec < 60) return `${sec.toFixed(sec < 10 ? 1 : 0).replace('.', ',')} giây`
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return s ? `${m} phút ${s} giây` : `${m} phút`
}

const rtf = new Intl.RelativeTimeFormat('vi', { numeric: 'auto' })

export function relativeTime(ts: number, nowSec = Date.now() / 1000): string {
  const d = ts - nowSec
  const a = Math.abs(d)
  if (a < 45) return 'vừa xong'
  if (a < 3600) return rtf.format(Math.round(d / 60), 'minute')
  if (a < 86400) return rtf.format(Math.round(d / 3600), 'hour')
  if (a < 86400 * 30) return rtf.format(Math.round(d / 86400), 'day')
  return new Date(ts * 1000).toLocaleDateString('vi-VN')
}

export function absoluteTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString('vi-VN', { dateStyle: 'medium', timeStyle: 'short' })
}

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} KB`
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`
  return `${(n / 1024 ** 3).toFixed(2)} GB`
}

export const STEP_LABEL: Record<StepStatus, string> = {
  pending: 'Chờ bước trước',
  ready: 'Sẵn sàng',
  waiting_resource: 'Chờ tài nguyên',
  running: 'Đang chạy',
  awaiting_human: 'Chờ bạn duyệt',
  succeeded: 'Xong',
  failed: 'Lỗi',
  cancelled: 'Đã huỷ',
  cached: 'Dùng lại',
  skipped: 'Bỏ qua'
}

export const RUN_LABEL: Record<RunStatus, string> = {
  running: 'Đang chạy',
  awaiting_human: 'Chờ duyệt',
  succeeded: 'Hoàn tất',
  failed: 'Lỗi',
  cancelled: 'Đã huỷ',
  interrupted: 'Bị gián đoạn'
}

export const LANE_LABEL: Record<Lane, string> = {
  gpu: 'GPU',
  blender: 'Blender',
  cloud: 'Cloud',
  cpu: 'CPU',
  none: 'Thủ công'
}

export const WAITING_LABEL: Record<string, string> = {
  gpu: 'Chờ GPU',
  blender: 'Chờ Blender',
  cloud: 'Chờ lượt cloud',
  cpu: 'Chờ CPU',
  comfyui: 'Đang khởi động ComfyUI',
  codex: 'Chờ Codex',
  claude: 'Chờ Claude'
}

// meta.waiting_for is a lane name while queued, or a resource name while that resource boots.
export function waitingLabel(waitingFor: unknown): string {
  if (waitingFor === 'blender') return 'Chờ / khởi động Blender'
  return WAITING_LABEL[String(waitingFor ?? '')] ?? 'Chờ tài nguyên'
}

export function waitingHint(waitingFor: unknown): string {
  if (waitingFor === 'gpu' || waitingFor === 'cloud' || waitingFor === 'cpu')
    return 'một bước khác đang dùng làn này. Bước sẽ tự chạy khi làn trống.'
  return 'tài nguyên đang được bật hoặc đang bận. Bước sẽ tự chạy khi sẵn sàng.'
}

const KIND_NAMES: Record<string, string> = {
  'comfy.t2i_zimage_turbo': 'Sinh ảnh (Z-Image)',
  'comfy.remove_bg_birefnet': 'Tách nền (BiRefNet)',
  'comfy.i23d_hunyuan': 'Dựng mesh 3D',
  'codex.image': 'Sinh ảnh (Codex)',
  'claude.task': 'Claude phân tích',
  'blender.render_dirs': 'Render các hướng',
  'op.trim_pad_resize': 'Cắt viền & căn cỡ',
  'op.sheet': 'Ghép sprite sheet',
  'op.mirror_dirs': 'Lật thành 8 hướng',
  'comfy.img2mesh_trellis2_textured': 'Dựng mesh (TRELLIS)',
  'comfy.segment_sam3': 'Tách part (SAM 3.1)',
  'comfy.inpaint_flux2_klein4b': 'Vá ảnh (Flux2 Klein)',
  'blender.cleanup': 'Dọn mesh (Blender)',
  'op.glb_validate': 'Kiểm tra GLB',
  'op.downscale': 'Thu nhỏ',
  'op.parts': 'Tách part (rig 2D)',
  'op.layers': 'Tách layer & khớp',
  'op.spine_export': 'Xuất Spine 4.2',
  'gate.pick': 'Chọn ảnh',
  'gate.approve': 'Duyệt kết quả'
}

export function kindName(kind: string): string {
  if (KIND_NAMES[kind]) return KIND_NAMES[kind]
  const [prefix, rest] = kind.split('.', 2)
  if (prefix === 'comfy' && rest) return `ComfyUI · ${rest.replace(/_/g, ' ')}`
  return kind
}

export const RESOURCE_NAMES: Record<string, string> = {
  comfyui: 'ComfyUI',
  blender: 'Blender',
  codex: 'Codex',
  claude: 'Claude'
}

// Step ids recipes reuse; a step whose kind is unknown falls back to these before showing the raw id.
const STEP_ID_NAMES: Record<string, string> = {
  gen: 'Sinh ảnh',
  concept: 'Sinh ảnh concept',
  brief: 'Viết brief',
  pick: 'Chọn ảnh',
  rmbg: 'Tách nền',
  fit: 'Căn cỡ',
  norm: 'Chuẩn hoá khung',
  edit: 'Sửa ảnh',
  angles: 'Sinh các góc',
  views: 'Sinh các góc',
  mesh: 'Dựng mesh 3D',
  build: 'Dựng mesh 3D',
  cleanup: 'Dọn mesh',
  validate: 'Kiểm tra GLB',
  check: 'Kiểm tra',
  turntable: 'Render xoay vòng',
  render: 'Render các hướng',
  dirs: 'Lật thành 8 hướng',
  down: 'Thu nhỏ',
  sheet: 'Ghép sprite sheet',
  segment: 'Tách part',
  parts: 'Tách part',
  bones: 'Chỉnh khớp',
  layers: 'Tách layer',
  fill: 'Vá chỗ khuất',
  export: 'Xuất Spine',
  approve: 'Duyệt kết quả'
}

const KIND_SHORT: Record<string, string> = {
  'gate.pick': 'Chọn ảnh',
  'gate.approve': 'Duyệt kết quả',
  'op.trim_pad_resize': 'Căn cỡ',
  'op.normalize': 'Chuẩn hoá khung',
  'op.mirror_dirs': 'Lật thành 8 hướng',
  'op.downscale': 'Thu nhỏ',
  'op.pixelate': 'Pixel art',
  'op.sheet': 'Ghép sprite sheet',
  'op.glb_validate': 'Kiểm tra GLB',
  'op.view_check': 'Kiểm tra góc nhìn',
  'op.parts': 'Tách part',
  'op.layers': 'Tách layer',
  'op.spine_export': 'Xuất Spine',
  'comfy.t2i_zimage_turbo': 'Sinh ảnh',
  'comfy.t2i_zimage_controlnet': 'Sinh ảnh theo khung',
  'comfy.remove_bg_birefnet': 'Tách nền',
  'comfy.img2mesh_hunyuan21': 'Dựng mesh 3D',
  'comfy.i23d_hunyuan': 'Dựng mesh 3D',
  'comfy.img2mesh_trellis2_textured': 'Dựng mesh 3D',
  'comfy.img2mesh_trellis2_mv_textured': 'Dựng mesh 3D',
  'comfy.edit_flux2_klein4b': 'Sửa ảnh',
  'comfy.multi_angles_qwen2511': 'Sinh các góc',
  'comfy.segment_sam3': 'Tách part',
  'comfy.inpaint_flux2_klein4b': 'Vá chỗ khuất',
  'views.qwen_checked': 'Sinh các góc',
  'mesh.checked': 'Dựng mesh 3D',
  'codex.image': 'Sinh ảnh',
  'claude.task': 'Claude phân tích',
  'claude.brief': 'Viết brief',
  'claude.procedural_3d': 'Dựng 3D bằng code',
  'blender.cleanup': 'Dọn mesh',
  'blender.render_dirs': 'Render các hướng',
  'blender.script': 'Chạy script Blender',
  'blender.agent': 'Blender tự động'
}

/** Short human step name from a kind (`gate.pick`) or recipe step id (`rmbg`). Use `kindName` for the long, tool-specific name. */
export function stepLabel(kindOrId: string, fallbackId?: string): string {
  if (KIND_SHORT[kindOrId]) return KIND_SHORT[kindOrId]
  if (STEP_ID_NAMES[kindOrId]) return STEP_ID_NAMES[kindOrId]
  if (fallbackId && STEP_ID_NAMES[fallbackId]) return STEP_ID_NAMES[fallbackId]
  const [prefix, rest] = kindOrId.split('.', 2)
  if (rest && prefix === 'comfy') return 'Bước ComfyUI'
  if (rest && prefix === 'blender') return 'Bước Blender'
  if (rest && prefix === 'claude') return 'Bước Claude'
  if (rest && prefix === 'op') return 'Xử lý'
  return kindOrId
}

/** stepLabel for a StepRow / RecipeStepInfo-like object. */
export function stepName(step: { kind: string; key?: string; id?: string }): string {
  return stepLabel(step.kind, step.key ?? step.id)
}

const ERROR_LABELS: Record<string, string> = {
  interrupted: 'Bị dừng giữa chừng',
  cancelled: 'Đã huỷ',
  rejected: 'Bị từ chối khi duyệt',
  internal: 'Lỗi nội bộ',
  blob_missing: 'Thiếu file trung gian',
  ref_unresolved: 'Thiếu dữ liệu từ bước trước',
  gate_not_runnable: 'Bước duyệt không chạy được',
  comfy_node_errors: 'Lỗi trong ComfyUI',
  comfy_execution_error: 'Lỗi trong ComfyUI',
  comfy_interrupted: 'ComfyUI bị dừng giữa chừng',
  comfy_timeout: 'ComfyUI chạy quá lâu',
  comfy_unreachable: 'Không kết nối được ComfyUI',
  comfy_unavailable: 'ComfyUI chưa sẵn sàng',
  comfy_boot_failed: 'Không khởi động được ComfyUI',
  comfy_boot_timeout: 'ComfyUI khởi động quá lâu',
  comfy_submit_failed: 'ComfyUI không nhận yêu cầu',
  comfy_no_output: 'ComfyUI không trả ảnh',
  comfy_no_history: 'ComfyUI không trả kết quả',
  comfy_missing_image: 'Thiếu ảnh đầu vào cho ComfyUI',
  comfy_too_many_images: 'Quá nhiều ảnh cho ComfyUI',
  comfy_bad_param: 'Tham số ComfyUI không hợp lệ',
  oom: 'Hết bộ nhớ GPU',
  cuda_oom: 'Hết bộ nhớ GPU',
  blender_missing: 'Không tìm thấy Blender',
  blender_boot_failed: 'Không khởi động được Blender',
  blender_crashed: 'Blender bị đóng đột ngột',
  blender_timeout: 'Blender chạy quá lâu',
  blender_script_error: 'Script Blender bị lỗi',
  blender_script_timeout: 'Script Blender chạy quá lâu',
  blender_no_output: 'Blender không trả kết quả',
  blender_agent_timeout: 'Blender tự động chạy quá lâu',
  blender_agent_no_output: 'Blender tự động không trả kết quả',
  codex_missing: 'Không tìm thấy Codex',
  codex_timeout: 'Codex chạy quá lâu',
  codex_no_image: 'Codex không trả ảnh',
  claude_failed: 'Claude bị lỗi',
  claude_error: 'Claude bị lỗi',
  claude_no_result: 'Claude không trả kết quả',
  claude_no_structured_output: 'Claude trả sai định dạng',
  claude_bad_output: 'Claude trả sai định dạng',
  glb_invalid: 'File GLB không hợp lệ',
  spine_invalid: 'File Spine không hợp lệ',
  views_rejected: 'Các góc nhìn không đạt',
  views_mismatch: 'Các góc nhìn không khớp nhau',
  parts_missing: 'Thiếu part nhân vật',
  parts_empty: 'Không tách được part',
  procedural_failed: 'Dựng 3D bằng code bị lỗi',
  op_no_input: 'Thiếu đầu vào',
  op_bad_input: 'Đầu vào không hợp lệ',
  op_bad_image: 'Ảnh không đọc được',
  unreachable: 'Không kết nối được công cụ'
}

/** Plain-language name for a step/run error code (`comfy_node_errors` → "Lỗi trong ComfyUI"). */
export function errorLabel(code: string | null | undefined): string {
  if (!code) return 'Lỗi không rõ'
  if (ERROR_LABELS[code]) return ERROR_LABELS[code]
  if (code.endsWith('_bad_params') || code.endsWith('_bad_param')) return 'Tham số không hợp lệ'
  if (code.endsWith('_timeout')) return 'Chạy quá lâu'
  if (code.endsWith('_missing')) return 'Thiếu công cụ'
  return 'Bước bị lỗi'
}

export const DIRECTION_LABEL: Record<string, string> = {
  down: 'Dưới',
  down_right: 'Dưới-phải',
  right: 'Phải',
  up_right: 'Trên-phải',
  up: 'Trên',
  up_left: 'Trên-trái',
  left: 'Trái',
  down_left: 'Dưới-trái',
  S: 'Dưới',
  SE: 'Dưới-phải',
  E: 'Phải',
  NE: 'Trên-phải',
  N: 'Trên',
  NW: 'Trên-trái',
  W: 'Trái',
  SW: 'Dưới-trái'
}

/** `down_right` / `SE` → "Dưới-phải"; unknown names pass through. */
export function directionLabel(dir: string): string {
  return DIRECTION_LABEL[dir] ?? DIRECTION_LABEL[dir.toLowerCase()] ?? dir
}

export function runCount(n: number): string {
  return `${n} lượt`
}
