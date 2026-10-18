import { closeSync, fstatSync, mkdirSync, openSync, renameSync, rmSync, writeSync } from 'node:fs'
import { open, readdir, stat } from 'node:fs/promises'
import { dirname, join } from 'node:path'

export const LOG_MAX_BYTES = 2 * 1024 * 1024
const LOG_KEEP = 3
const MAX_LINE = 2000

const REDACTIONS: Array<[RegExp, string]> = [
  [/(bearer\s+)[^\s"',;]+/gi, '$1***'],
  [/([?&]t=)[^&\s"'#]+/g, '$1***'],
  [/(token["']?\s*[:=]\s*["']?)[A-Za-z0-9_\-.~+/=]{6,}/gi, '$1***']
]

export function redact(text: string, secrets: Iterable<string> = []): string {
  let out = text
  for (const s of secrets) if (s) out = out.split(s).join('***')
  for (const [re, repl] of REDACTIONS) out = out.replace(re, repl)
  return out
}

/** Append-only file that rolls `name.log` → `name.1.log` → `name.2.log` once it passes `maxBytes`. */
export class RotatingLog {
  private fd: number | null = null
  private size = 0

  constructor(
    readonly file: string,
    private readonly maxBytes = LOG_MAX_BYTES,
    private readonly keep = LOG_KEEP
  ) {}

  write(line: string): void {
    try {
      if (this.fd === null) this.open()
      const buf = Buffer.from(line.endsWith('\n') ? line : `${line}\n`, 'utf8')
      if (this.size > 0 && this.size + buf.length > this.maxBytes) this.rotate()
      writeSync(this.fd!, buf)
      this.size += buf.length
    } catch {
      // A full disk or locked file must never take the app down with it.
    }
  }

  close(): void {
    if (this.fd !== null) closeSync(this.fd)
    this.fd = null
  }

  private open(): void {
    mkdirSync(dirname(this.file), { recursive: true })
    this.fd = openSync(this.file, 'a')
    this.size = fstatSync(this.fd).size
  }

  private rotated(i: number): string {
    return i === 0 ? this.file : this.file.replace(/\.log$/, `.${i}.log`)
  }

  private rotate(): void {
    this.close()
    rmSync(this.rotated(this.keep - 1), { force: true })
    for (let i = this.keep - 2; i >= 0; i--) {
      try {
        renameSync(this.rotated(i), this.rotated(i + 1))
      } catch {
        /* missing generation */
      }
    }
    this.open()
  }
}

export type LogLevel = 'info' | 'warn' | 'error'

export class Devlog {
  private readonly appLog: RotatingLog
  private readonly coreLog: RotatingLog
  private readonly rendererLog: RotatingLog

  constructor(
    readonly dir: string,
    private readonly secrets: () => Iterable<string>
  ) {
    this.appLog = new RotatingLog(join(dir, 'app.log'))
    this.coreLog = new RotatingLog(join(dir, 'core.log'))
    this.rendererLog = new RotatingLog(join(dir, 'renderer.log'))
  }

  private format(level: string, msg: string): string {
    const clean = redact(msg, this.secrets()).replace(/\r?\n/g, '\n    ')
    return `${new Date().toISOString()} ${level.toUpperCase().padEnd(5)} ${clean.length > MAX_LINE * 4 ? `${clean.slice(0, MAX_LINE * 4)}…` : clean}`
  }

  app(level: LogLevel, msg: string): void {
    this.appLog.write(this.format(level, msg))
  }

  core(stream: 'out' | 'err', line: string): void {
    this.coreLog.write(this.format(stream, line.length > MAX_LINE ? `${line.slice(0, MAX_LINE)}…` : line))
  }

  renderer(level: LogLevel, msg: string): void {
    this.rendererLog.write(this.format(level, msg))
  }

  redact(text: string): string {
    return redact(text, this.secrets())
  }

  close(): void {
    this.appLog.close()
    this.coreLog.close()
    this.rendererLog.close()
  }
}

/** Fixed-window limiter so a renderer error loop cannot flood the disk. */
export class RateLimiter {
  private windowStart = 0
  private count = 0
  dropped = 0

  constructor(
    private readonly max: number,
    private readonly windowMs: number
  ) {}

  /** Returns how many lines were dropped in the previous window when a new one opens, else 0; null = drop this one. */
  take(now = Date.now()): number | null {
    if (now - this.windowStart >= this.windowMs) {
      const dropped = this.dropped
      this.windowStart = now
      this.count = 0
      this.dropped = 0
      this.count++
      return dropped
    }
    if (this.count >= this.max) {
      this.dropped++
      return null
    }
    this.count++
    return 0
  }
}

export interface LogFileInfo {
  name: string
  bytes: number
  modified: number
}

const ORDER = ['app.log', 'core.log', 'renderer.log']

function rank(name: string): number {
  const base = name.replace(/\.\d+\.log$/, '.log')
  const i = ORDER.indexOf(base)
  return (i < 0 ? ORDER.length : i) * 100 + (base === name ? 0 : Number(name.match(/\.(\d+)\.log$/)?.[1] ?? 0))
}

export async function listLogs(dir: string): Promise<LogFileInfo[]> {
  let names: string[]
  try {
    names = (await readdir(dir)).filter((n) => n.toLowerCase().endsWith('.log'))
  } catch {
    return []
  }
  const out: LogFileInfo[] = []
  for (const name of names) {
    try {
      const s = await stat(join(dir, name))
      if (s.isFile()) out.push({ name, bytes: s.size, modified: s.mtimeMs })
    } catch {
      /* vanished between readdir and stat */
    }
  }
  return out.sort((a, b) => rank(a.name) - rank(b.name) || a.name.localeCompare(b.name))
}

/** Last `maxBytes` of a file as text; the first partial line is dropped when the read starts mid-file. */
export async function readTail(file: string, maxBytes: number): Promise<{ text: string; truncated: boolean }> {
  const fh = await open(file, 'r')
  try {
    const { size } = await fh.stat()
    const start = Math.max(0, size - maxBytes)
    const buf = Buffer.alloc(size - start)
    await fh.read(buf, 0, buf.length, start)
    let text = buf.toString('utf8')
    if (start > 0) text = text.slice(text.indexOf('\n') + 1)
    return { text, truncated: start > 0 }
  } finally {
    await fh.close()
  }
}

export function lastLines(text: string, n: number): string[] {
  const lines = text.split(/\r?\n/)
  if (lines.at(-1) === '') lines.pop()
  return lines.slice(-n).map((l) => (l.length > MAX_LINE ? `${l.slice(0, MAX_LINE)}…` : l))
}
