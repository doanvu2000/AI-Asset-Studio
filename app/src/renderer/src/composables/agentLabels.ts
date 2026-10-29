import type { AgentActivity } from '@/api/types'

export function clientName(client: string): string {
  const c = client.toLowerCase()
  if (c.includes('claude')) return 'Claude Code'
  if (c.includes('codex')) return 'Codex'
  return client || 'Agent'
}

const TOOL: Record<string, string> = {
  list_recipes: 'xem danh sách quy trình',
  describe_recipe: 'đọc quy trình',
  start_run: 'bắt đầu lượt chạy',
  generate: 'tạo asset (chạy tới hết)',
  wait_run: 'đợi lượt chạy',
  get_run: 'xem lượt chạy',
  resolve_gate: 'chọn / duyệt ở bước chờ',
  get_outputs: 'lấy file kết quả',
  cancel_run: 'huỷ lượt chạy',
  retry_step: 'chạy lại bước',
  system_status: 'kiểm tra hệ thống',
  create_project: 'tạo dự án',
  update_project: 'sửa dự án / khoá style',
  import_to_library: 'nhập file vào Thư viện',
  follow_up_options: 'tìm bước dùng tiếp',
  export_to_engine: 'xuất vào project game',
  engine_preview: 'mở thử trong Unity/Godot',
  build_asset_pack: 'đóng gói asset',
  project_qa: 'kiểm tra đồng bộ dự án',
  restyle_scan: 'quét thư mục để đổi style',
  restyle_apply: 'ghi style mới vào project',
  restyle_undo: 'hoàn tác đổi style'
}

export const toolLabel = (tool: string | null | undefined): string => (tool ? (TOOL[tool] ?? tool) : '')

export function activityText(e: AgentActivity): string {
  switch (e.phase) {
    case 'connect':
      return 'đã kết nối MCP'
    case 'disconnect':
      return 'đã ngắt kết nối'
    case 'wait':
      return e.detail || 'đang đợi'
    case 'error':
      return `lỗi: ${toolLabel(e.tool)}${e.detail ? ` — ${e.detail}` : ''}`
    case 'end':
      return `xong: ${toolLabel(e.tool)}${e.seconds != null && e.seconds >= 1 ? ` (${Math.round(e.seconds)} giây)` : ''}`
    default:
      return toolLabel(e.tool)
  }
}
