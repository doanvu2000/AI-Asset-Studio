import { join } from 'node:path'
import type { CoreDiagnostics, DiagInfo, DiagPayload } from '../preload/types'
import { lastLines, readTail, type Devlog } from './devlog'
import { buildZip, type ZipEntry } from './zip'

export const REPORT_MAX_CHARS = 2 * 1024 * 1024
const PAYLOAD_MAX_CHARS = 4 * 1024 * 1024
const REPORT_TAIL_LINES = 200
const REPORT_TAIL_BYTES = 512 * 1024
const BUNDLE_FILE_MAX_BYTES = 8 * 1024 * 1024

export function parsePayload(raw: unknown): DiagPayload {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('invalid payload')
  const p = raw as Record<string, unknown>
  const error = typeof p.error === 'string' ? p.error.slice(0, 2000) : undefined
  const d = p.diagnostics
  if (d === null || d === undefined) return { diagnostics: null, error }
  if (typeof d !== 'object' || Array.isArray(d)) throw new Error('invalid diagnostics')
  if (JSON.stringify(d).length > PAYLOAD_MAX_CHARS) return { diagnostics: null, error: 'diagnostics quá lớn, đã bỏ' }
  return { diagnostics: d as CoreDiagnostics, error }
}

const arr = <T>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : [])
const iso = (secs: unknown): string => (typeof secs === 'number' ? new Date(secs * 1000).toISOString() : '?')
const yes = (b: boolean): string => (b ? 'có' : 'không')

function headerLines(info: DiagInfo): string[] {
  const c = info.core
  return [
    `# AI Asset Studio — nhật ký chẩn đoán`,
    '',
    `- Thời điểm: ${new Date().toISOString()}`,
    `- App: ${info.appVersion} · Electron ${info.electron} · Chrome ${info.chrome} · Node ${info.node}`,
    `- Nền tảng: ${info.platform} ${info.arch} · OS ${info.osRelease}`,
    `- Đóng gói: ${yes(info.packaged)} · Portable: ${yes(info.portable)} · Mock: ${yes(info.mock)}`,
    `- Thư mục dữ liệu: ${info.dataDir}`,
    `- Thư mục log: ${info.logsDir}`,
    `- Core: ${c.state} (lần ${c.attempt})${c.pid ? ` · pid ${c.pid}` : ''}${c.attached ? ' · dùng chung' : ''}${c.baseUrl ? ` · ${c.baseUrl}` : ''}`,
    ...(c.message ? [`- Core message: ${c.message}`] : []),
    ...(c.detail ? ['', '```', c.detail, '```'] : [])
  ]
}

function diagnosticsLines(p: DiagPayload): string[] {
  const d = p.diagnostics
  if (!d) return ['## Core', '', `Không lấy được chẩn đoán từ core${p.error ? `: ${p.error}` : '.'}`]
  const core = d.core ?? ({} as CoreDiagnostics['core'])
  const out = [
    '## Core',
    '',
    `- Phiên bản: ${core.version ?? '?'} · Python ${core.python ?? '?'} · frozen: ${core.frozen ? 'có' : 'không'} · pid ${core.pid ?? '?'}`,
    `- Khởi động: ${iso(core.started_at)} · ${core.platform ?? ''}`,
    `- Data dir: ${d.data_dir ?? '?'}`,
    `- Recipe: ${d.counts?.recipes ?? '?'} · Kind: ${d.counts?.kinds ?? '?'}`,
    '',
    '### Công cụ',
    '',
    ...arr<CoreDiagnostics['tools'][number]>(d.tools).map(
      (t) => `- ${t.exists ? (t.ok ? '✓' : '!') : '✗'} ${t.label ?? t.key}: ${t.path}${t.detail ? ` (${t.detail})` : ''}`
    ),
    '',
    '### Tài nguyên',
    '',
    ...arr<CoreDiagnostics['resources'][number]>(d.resources).map((r) => `- ${r.name}: ${r.status} ${JSON.stringify(r.detail ?? {})}`)
  ]
  const failed = arr<CoreDiagnostics['failed_steps'][number]>(d.failed_steps)
  out.push('', `### Bước lỗi gần đây (${failed.length})`, '')
  if (!failed.length) out.push('Không có.')
  for (const f of failed) out.push(`- ${iso(f.ended_at)} · run ${f.run_id} · ${f.key} (${f.kind}, lần ${f.attempt}) · ${f.code ?? '?'}: ${f.message}`)
  return out
}

async function logSections(info: DiagInfo, devlog: Devlog, budget: number): Promise<string[]> {
  const current = info.logs.filter((l) => !/\.\d+\.log$/.test(l.name))
  const per = Math.max(4096, Math.floor(budget / Math.max(1, current.length)))
  const out: string[] = []
  for (const l of current) {
    out.push('', `## ${l.name} (${l.bytes} byte)`, '')
    try {
      const { text } = await readTail(join(info.logsDir, l.name), REPORT_TAIL_BYTES)
      let body = devlog.redact(lastLines(text, REPORT_TAIL_LINES).join('\n'))
      if (body.length > per) body = `…\n${body.slice(body.length - per)}`
      out.push('```', body || '(trống)', '```')
    } catch (e) {
      out.push(`Không đọc được: ${(e as Error).message}`)
    }
  }
  return out
}

export async function buildReport(info: DiagInfo, payload: DiagPayload, devlog: Devlog): Promise<string> {
  const head = devlog.redact([...headerLines(info), '', ...diagnosticsLines(payload)].join('\n'))
  const logs = await logSections(info, devlog, REPORT_MAX_CHARS - head.length - 1024)
  const report = `${head}\n${logs.join('\n')}\n`
  return report.length > REPORT_MAX_CHARS ? report.slice(0, REPORT_MAX_CHARS) : report
}

export async function buildBundle(info: DiagInfo, payload: DiagPayload, devlog: Devlog): Promise<{ zip: Buffer; files: number }> {
  const entries: ZipEntry[] = [
    { name: 'report.md', data: Buffer.from(await buildReport(info, payload, devlog), 'utf8') },
    { name: 'app-info.json', data: Buffer.from(devlog.redact(JSON.stringify(info, null, 2)), 'utf8') }
  ]
  if (payload.diagnostics) {
    entries.push({ name: 'diagnostics.json', data: Buffer.from(devlog.redact(JSON.stringify(payload.diagnostics, null, 2)), 'utf8') })
  }
  for (const l of info.logs) {
    try {
      const { text, truncated } = await readTail(join(info.logsDir, l.name), BUNDLE_FILE_MAX_BYTES)
      const body = (truncated ? '…(đã cắt phần đầu)\n' : '') + devlog.redact(text)
      entries.push({ name: `logs/${l.name}`, data: Buffer.from(body, 'utf8'), mtime: new Date(l.modified) })
    } catch {
      /* locked or vanished: the rest of the bundle is still useful */
    }
  }
  return { zip: buildZip(entries), files: entries.length }
}
