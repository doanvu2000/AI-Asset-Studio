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
