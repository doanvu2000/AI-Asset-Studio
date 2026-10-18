import type { StepErrorInfo } from '@/api/types'

export interface AdviceLink {
  label: string
  to: string
}

export interface ErrorAdvice {
  title: string
  hint: string
  retryLabel: string
  links: AdviceLink[]
}

const TO_RESOURCES: AdviceLink = { label: 'Xem tài nguyên', to: '/' }
const TO_TOOLS: AdviceLink = { label: 'Mở Cài đặt đường dẫn', to: '/settings' }

const OOM_RE = /out of memory|outofmemory|cuda error|cuda_error|\boom\b/i

function base(title: string, hint: string, links: AdviceLink[] = [], retryLabel = 'Thử lại'): ErrorAdvice {
  return { title, hint, retryLabel, links }
}

export function errorAdvice(err: StepErrorInfo | null | undefined): ErrorAdvice {
  const code = err?.code ?? ''
  const text = `${err?.message ?? ''} ${typeof err?.detail === 'string' ? err.detail : JSON.stringify(err?.detail ?? '')}`

  if (OOM_RE.test(text) || OOM_RE.test(code)) {
    return base(
      'GPU hết bộ nhớ (VRAM)',
      'Đóng Blender hoặc ứng dụng khác đang dùng GPU, hoặc tắt ComfyUI để giải phóng VRAM, rồi thử lại.',
      [TO_RESOURCES]
    )
  }
  if (code === 'interrupted' || code === 'comfy_interrupted') {
    return base(
      'Bị dừng giữa chừng',
      'Bước đang chạy thì core hoặc ComfyUI khởi động lại. Các bước đã xong vẫn được giữ, chỉ cần chạy tiếp.',
      [],
      'Chạy tiếp'
    )
  }
  if (code === 'comfy_node_errors' || code === 'comfy_execution_error') {
    return base('Lỗi trong ComfyUI', 'ComfyUI không chạy được workflow, thường do thiếu model hoặc custom node. Xem chi tiết kỹ thuật bên dưới.')
  }
  if (/^comfy_(unreachable|unavailable|boot_failed|boot_timeout|submit_failed)$/.test(code)) {
    return base('Không kết nối được ComfyUI', 'Kiểm tra ComfyUI đã bật chưa và đường dẫn cài đặt có đúng không.', [TO_RESOURCES, TO_TOOLS])
  }
  if (code === 'blender_missing' || code === 'codex_missing') {
    const tool = code.startsWith('blender') ? 'Blender' : 'Codex'
    return base(`Không tìm thấy ${tool}`, `Chỉ đường dẫn tới ${tool} trong Cài đặt rồi thử lại.`, [TO_TOOLS])
  }
  if (code === 'blender_crashed' || code === 'blender_boot_failed') {
    return base('Blender dừng đột ngột', 'Thường do thiếu bộ nhớ hoặc add-on lỗi. Đóng các cửa sổ Blender khác rồi thử lại.', [TO_RESOURCES])
  }
  if (code.endsWith('_timeout')) {
    return base('Quá thời gian chờ', 'Máy có thể đang bận việc khác. Thử lại khi GPU hoặc Blender rảnh.', [TO_RESOURCES])
  }
  if (code.startsWith('claude_')) {
    return base('Claude không trả về kết quả dùng được', 'Thử lại thường sẽ ổn. Nếu lặp lại, kiểm tra kết nối mạng và đăng nhập Claude.')
  }
  if (code === 'codex_no_image') {
    return base('Codex không tạo ra ảnh', 'Thử lại, hoặc viết prompt cụ thể hơn.')
  }
  if (code === 'rejected' || code === 'views_rejected') {
    return base('Kết quả bị từ chối', 'Thử lại để tạo phương án mới.')
  }
  if (/bad_param/.test(code)) {
    return base('Tham số không hợp lệ', 'Kiểm tra tham số của bước này trong quy trình rồi chạy lại.')
  }
  if (/bad_input|no_input|missing_image|blob_missing|ref_unresolved|too_many_images/.test(code)) {
    return base('Thiếu hoặc sai dữ liệu đầu vào', 'Bước trước không đưa ra dữ liệu như bước này cần. Xem lại kết quả bước trước.')
  }
  if (/no_output|no_history/.test(code)) {
    return base('Bước chạy xong nhưng không có kết quả', 'Thử lại. Nếu lặp lại, xem chi tiết kỹ thuật.')
  }
  if (code === 'glb_invalid' || code === 'spine_invalid') {
    return base('Kết quả tạo ra bị hỏng', 'File đầu ra không hợp lệ. Thử lại để tạo lại.')
  }
  if (code === 'internal') {
    return base('Lỗi bên trong ứng dụng', 'Thử lại. Nếu lặp lại, xuất nhật ký ở Cài đặt → Chẩn đoán để báo lỗi.', [TO_TOOLS])
  }
  return base('Bước này bị lỗi', err?.message ? 'Xem chi tiết kỹ thuật bên dưới.' : 'Không rõ nguyên nhân.')
}
