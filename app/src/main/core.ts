import { spawn, execFile, type ChildProcess } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { EventEmitter } from 'node:events'
import type { CoreConnection, CoreStatus } from '../preload/types'

const READY_PREFIX = 'STUDIO_CORE_READY '
const READY_TIMEOUT_MS = 20_000
const BACKOFF_MAX_MS = 30_000
const STABLE_AFTER_MS = 60_000

export interface CoreOptions {
  repoRoot: string
  mock: boolean
}

export class CoreSupervisor extends EventEmitter {
  readonly token = randomBytes(32).toString('hex')
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
    this.spawnOnce()
  }

  private pythonPath(): string {
    return process.env.STUDIO_PYTHON ?? join(this.opts.repoRoot, 'core', '.venv', 'Scripts', 'python.exe')
  }

  private spawnOnce(): void {
    const python = this.pythonPath()
    const attempt = this.status.attempt + 1
    if (!existsSync(python)) {
      this.setStatus({ state: 'error', attempt, message: `Không tìm thấy Python của core: ${python}` })
      return
    }
    this.setStatus({ state: 'starting', attempt })
    this.stderrTail = []

    const child = spawn(python, ['-m', 'studio_core', 'serve', '--port', '0'], {
      cwd: join(this.opts.repoRoot, 'core'),
      env: { ...process.env, STUDIO_TOKEN: this.token, STUDIO_PARENT_PID: String(process.pid), PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' },
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe']
    })
    this.child = child

    const timeout = setTimeout(() => {
      if (this.child === child && this.status.state === 'starting') {
        this.lastMessage = 'Core không báo sẵn sàng sau 20 giây'
        killTree(child.pid)
      }
    }, READY_TIMEOUT_MS)

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
      if (!this.stopping) this.spawnOnce()
    }, delay)
  }

  async stop(): Promise<void> {
    this.stopping = true
    if (this.restartTimer) clearTimeout(this.restartTimer)
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
