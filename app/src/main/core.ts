import { spawn, execFile, type ChildProcess } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { existsSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { EventEmitter } from 'node:events'
import type { CoreConnection, CoreStatus } from '../preload/types'

const READY_PREFIX = 'STUDIO_CORE_READY '
const READY_TIMEOUT_MS = 20_000
// First launch of the frozen core is slowed by Defender scanning the freshly installed _internal/ DLLs.
const READY_TIMEOUT_PACKAGED_MS = 60_000
const BACKOFF_MAX_MS = 30_000
const STABLE_AFTER_MS = 60_000
const ATTACH_WATCH_MS = 3_000
// Must match EXIT_ALREADY_RUNNING in core/studio_core/mcp/lockfile.py.
const EXIT_ALREADY_RUNNING = 3

export interface CoreOptions {
  repoRoot: string
  mock: boolean
  packaged: boolean
  resourcesPath: string
  appDataPath: string
}

export interface CoreLaunch {
  command: string
  args: string[]
}

// Must match APP_NAME in core/studio_core/paths.py (frozen default data dir).
const APP_DATA_NAME = 'AI Asset Studio'

interface CoreLock {
  pid: number
  host: string
  port: number
  token: string
}

function parseCoreLock(text: string): CoreLock | null {
  try {
    const d = JSON.parse(text) as Record<string, unknown>
    if (!Number.isInteger(d.port) || !Number.isInteger(d.pid) || typeof d.token !== 'string' || !d.token) return null
    const host = typeof d.host === 'string' && d.host && d.host !== '0.0.0.0' && d.host !== '::' ? d.host : '127.0.0.1'
    return { pid: d.pid as number, host, port: d.port as number, token: d.token }
  } catch {
    return null
  }
}

function pidAlive(pid: number): boolean {
  if (pid <= 0) return false
  try {
    process.kill(pid, 0)
    return true
  } catch (e) {
    return (e as NodeJS.ErrnoException).code === 'EPERM'
  }
}

export class CoreSupervisor extends EventEmitter {
  private readonly ownToken = randomBytes(32).toString('hex')
  private token = this.ownToken
  private attachedPid: number | null = null
  private attachWatch: NodeJS.Timeout | null = null
  private child: ChildProcess | null = null
  private status: CoreStatus = { state: 'stopped', attempt: 0 }
  private stopping = false
  private restartTimer: NodeJS.Timeout | null = null
  private readyWaiters: Array<(c: CoreConnection) => void> = []
  private stderrTail: string[] = []
  private readyAt = 0
  private failures = 0

  constructor(private readonly opts: CoreOptions) {
    super()
  }

  get current(): CoreStatus {
    return this.status
  }

  get connection(): CoreConnection | null {
    return this.status.state === 'ready' && this.status.baseUrl
      ? { baseUrl: this.status.baseUrl, token: this.opts.mock ? 'mock' : this.token }
      : null
  }

  waitReady(): Promise<CoreConnection> {
    const c = this.connection
    if (c) return Promise.resolve(c)
    return new Promise((resolve) => this.readyWaiters.push(resolve))
  }

  start(): void {
    this.stopping = false
    if (this.opts.mock) {
      this.setStatus({ state: 'ready', baseUrl: 'mock://core', attempt: 0, mock: true })
      return
    }
    void this.connect()
  }

  get coreDir(): string {
    return this.opts.packaged ? join(this.opts.resourcesPath, 'core') : join(this.opts.repoRoot, 'core')
  }

  // Mirrors core/studio_core/paths.py: STUDIO_DATA_DIR relative to the core's cwd, else %APPDATA% when frozen, else <repo>/data.
  get dataDir(): string {
    const env = process.env.STUDIO_DATA_DIR
    if (env) return resolve(this.coreDir, env)
    return this.opts.packaged ? join(this.opts.appDataPath, APP_DATA_NAME, 'data') : join(this.opts.repoRoot, 'data')
  }

  /** How to start the core CLI: the frozen exe when packaged, `python -m studio_core` in dev. */
  launch(...args: string[]): CoreLaunch {
    if (this.opts.packaged) return { command: join(this.coreDir, 'studio-core.exe'), args }
    return { command: this.pythonPath(), args: ['-m', 'studio_core', ...args] }
  }

  private async connect(): Promise<void> {
    if (this.stopping) return
    if (!(await this.tryAttach()) && !this.stopping) this.spawnOnce()
  }

  /** Reuses a core another client (MCP server) already runs on our data dir instead of starting a second one. */
  private async tryAttach(): Promise<boolean> {
    let lock: CoreLock | null
    try {
      lock = parseCoreLock(await readFile(join(this.dataDir, 'core.lock.json'), 'utf8'))
    } catch {
      return false
    }
    if (!lock || !pidAlive(lock.pid)) return false
    const baseUrl = `http://${lock.host}:${lock.port}`
    try {
      const res = await fetch(`${baseUrl}/api/runs?limit=1`, {
        headers: { Authorization: `Bearer ${lock.token}` },
        signal: AbortSignal.timeout(3000)
      })
      if (!res.ok) return false
    } catch {
      return false
    }
    if (this.stopping) return false
    this.token = lock.token
    this.attachedPid = lock.pid
    this.readyAt = Date.now()
    const pid = lock.pid
    this.attachWatch = setInterval(() => {
      if (!pidAlive(pid)) this.onAttachedLost()
    }, ATTACH_WATCH_MS)
    this.setStatus({ state: 'ready', attempt: this.status.attempt + 1, baseUrl, pid, attached: true })
    return true
  }

  private detach(): void {
    if (this.attachWatch) clearInterval(this.attachWatch)
    this.attachWatch = null
    this.attachedPid = null
    this.token = this.ownToken
  }

  private onAttachedLost(): void {
    this.detach()
    this.lastMessage = 'Core dùng chung đã dừng'
    this.scheduleRestart(null)
  }

  pythonPath(): string {
    return process.env.STUDIO_PYTHON ?? join(this.opts.repoRoot, 'core', '.venv', 'Scripts', 'python.exe')
  }

  private spawnOnce(): void {
    const { command, args } = this.launch('serve', '--port', '0')
    const attempt = this.status.attempt + 1
    if (!existsSync(command)) {
      const what = this.opts.packaged ? 'file chạy core' : 'Python của core'
      this.setStatus({ state: 'error', attempt, message: `Không tìm thấy ${what}: ${command}` })
      return
    }
    this.setStatus({ state: 'starting', attempt })
    this.stderrTail = []

    const child = spawn(command, args, {
      cwd: this.coreDir,
      env: { ...process.env, STUDIO_TOKEN: this.token, STUDIO_PARENT_PID: String(process.pid), PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' },
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe']
    })
    this.child = child

    const readyTimeout = this.opts.packaged ? READY_TIMEOUT_PACKAGED_MS : READY_TIMEOUT_MS
    const timeout = setTimeout(() => {
      if (this.child === child && this.status.state === 'starting') {
        this.lastMessage = `Core không báo sẵn sàng sau ${readyTimeout / 1000} giây`
        killTree(child.pid)
      }
    }, readyTimeout)

    let buf = ''
    child.stdout?.setEncoding('utf8')
    child.stdout?.on('data', (chunk: string) => {
      buf += chunk
      let nl: number
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl).trim()
        buf = buf.slice(nl + 1)
        if (line.startsWith(READY_PREFIX) && this.status.state === 'starting') {
          clearTimeout(timeout)
          this.onReadyLine(line.slice(READY_PREFIX.length), attempt)
        }
      }
    })
    child.stderr?.setEncoding('utf8')
    child.stderr?.on('data', (chunk: string) => {
      for (const l of chunk.split(/\r?\n/)) if (l.trim()) this.stderrTail.push(l)
      if (this.stderrTail.length > 40) this.stderrTail.splice(0, this.stderrTail.length - 40)
    })
    child.on('error', (err) => {
      this.lastMessage = err.message
    })
    child.on('exit', (code) => {
      clearTimeout(timeout)
      if (this.child !== child) return
      this.child = null
      if (this.stopping) {
        this.setStatus({ state: 'stopped', attempt })
        return
      }
      if (code === EXIT_ALREADY_RUNNING) {
        void this.tryAttach().then((ok) => {
          if (!ok && !this.stopping) this.scheduleRestart(code)
        })
        return
      }
      this.scheduleRestart(code)
    })
  }

  private lastMessage: string | undefined

  private onReadyLine(json: string, attempt: number): void {
    try {
      const info = JSON.parse(json) as { port: number; pid: number }
      this.readyAt = Date.now()
      this.setStatus({ state: 'ready', attempt, baseUrl: `http://127.0.0.1:${info.port}`, pid: info.pid })
    } catch {
      this.lastMessage = `Dòng READY không hợp lệ: ${json}`
      killTree(this.child?.pid)
    }
  }

  private scheduleRestart(code: number | null): void {
    if (this.readyAt && Date.now() - this.readyAt > STABLE_AFTER_MS) this.failures = 0
    this.failures += 1
    this.readyAt = 0
    const delay = Math.min(BACKOFF_MAX_MS, 1000 * 2 ** (this.failures - 1))
    const tail = this.stderrTail.slice(-6).join('\n')
    this.setStatus({
      state: 'restarting',
      attempt: this.status.attempt,
      retryInMs: delay,
      message: this.lastMessage ?? `Core thoát (mã ${code ?? '?'})`,
      detail: tail || undefined
    })
    this.lastMessage = undefined
    this.restartTimer = setTimeout(() => {
      this.restartTimer = null
      void this.connect()
    }, delay)
  }

  async stop(): Promise<void> {
    this.stopping = true
    if (this.restartTimer) clearTimeout(this.restartTimer)
    if (this.attachedPid !== null) {
      // Not ours: its spawner (MCP server) decides when it exits.
      this.detach()
      this.setStatus({ state: 'stopped', attempt: this.status.attempt })
      return
    }
    const child = this.child
    if (!child) return
    const exited = new Promise<void>((r) => child.once('exit', () => r()))
    const base = this.status.baseUrl
    if (base && this.status.state === 'ready') {
      try {
        await fetch(`${base}/api/shutdown`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${this.token}` },
          signal: AbortSignal.timeout(3000)
        })
      } catch {
        // Fall through to the hard kill below.
      }
    }
    const graceful = await Promise.race([exited.then(() => true), delay(4000).then(() => false)])
    if (!graceful) {
      killTree(child.pid)
      await Promise.race([exited, delay(2000)])
    }
  }

  async hasActiveRuns(): Promise<boolean> {
    const c = this.connection
    if (!c || this.opts.mock) return false
    try {
      const res = await fetch(`${c.baseUrl}/api/runs?status=running&limit=1`, {
        headers: { Authorization: `Bearer ${c.token}` },
        signal: AbortSignal.timeout(1500)
      })
      if (!res.ok) return false
      const rows = (await res.json()) as unknown[]
      return rows.length > 0
    } catch {
      return false
    }
  }

  private setStatus(s: CoreStatus): void {
    this.status = s
    this.emit('status', s)
    const c = this.connection
    if (c) {
      const waiters = this.readyWaiters
      this.readyWaiters = []
      for (const w of waiters) w(c)
    }
  }
}

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}

// The core spawns ComfyUI/Blender children; killing only python.exe would orphan them.
function killTree(pid: number | undefined): void {
  if (!pid) return
  if (process.platform === 'win32') {
    execFile('taskkill', ['/T', '/F', '/PID', String(pid)], { windowsHide: true }, () => {})
  } else {
    try {
      process.kill(-pid, 'SIGKILL')
    } catch {
      try {
        process.kill(pid, 'SIGKILL')
      } catch {
        /* already gone */
      }
    }
  }
}
