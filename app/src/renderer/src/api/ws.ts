import type { StudioEvent } from './types'

export type SocketState = 'connecting' | 'open' | 'reconnecting' | 'closed' | 'auth_error'

export interface EventSink {
  batch(events: StudioEvent[]): void
  // Server seq went backwards (core restarted) or a gap could not be replayed: caller must refetch.
  resync(): void
  state(s: SocketState): void
}

export interface EventStream {
  start(sink: EventSink): void
  stop(): void
}

// Collapses bursts of WS events into one store update per animation frame.
export class FrameBatcher {
  private queue: StudioEvent[] = []
  private scheduled = false

  constructor(private readonly flush: (events: StudioEvent[]) => void) {}

  push(e: StudioEvent): void {
    this.queue.push(e)
    if (this.scheduled) return
    this.scheduled = true
    requestAnimationFrame(() => {
      this.scheduled = false
      const q = this.queue
      this.queue = []
      if (q.length) this.flush(q)
    })
  }
}

const BACKOFF = [300, 600, 1200, 2500, 5000, 8000]

export class StudioSocket implements EventStream {
  private ws: WebSocket | null = null
  private lastSeq = 0
  private attempts = 0
  private neverOpened = 0
  private timer: number | undefined
  private stopped = false
  private sink: EventSink | null = null
  private batcher: FrameBatcher | null = null

  constructor(
    private readonly baseUrl: string,
    private readonly token: string
  ) {}

  start(sink: EventSink): void {
    this.sink = sink
    this.batcher = new FrameBatcher((events) => sink.batch(events))
    this.stopped = false
    this.connect()
  }

  stop(): void {
    this.stopped = true
    window.clearTimeout(this.timer)
    this.ws?.close()
    this.ws = null
    this.sink?.state('closed')
  }

  private connect(): void {
    const url = `${this.baseUrl.replace(/^http/, 'ws')}/ws?t=${encodeURIComponent(this.token)}`
    this.sink?.state(this.attempts === 0 ? 'connecting' : 'reconnecting')
    const ws = new WebSocket(url)
    this.ws = ws

    ws.onmessage = (msg) => {
      let data: unknown
      try {
        data = JSON.parse(String(msg.data))
      } catch {
        return
      }
      const ev = data as StudioEvent & { type: string }
      if (ev.type === 'hello') {
        this.onHello(ws, (data as { seq: number }).seq)
        return
      }
      if (ev.type === 'ping') return
      if (ev.type === 'stream.dropped') {
        // Server dropped us for being slow; reconnecting replays from lastSeq via hello/resume.
        ws.close()
        return
      }
      if (typeof ev.seq !== 'number' || ev.seq <= this.lastSeq) return
      this.lastSeq = ev.seq
      this.batcher?.push(ev)
    }
    let opened = false
    ws.onopen = () => {
      opened = true
      this.attempts = 0
      this.neverOpened = 0
      this.sink?.state('open')
    }
    ws.onclose = () => {
      if (this.ws !== ws || this.stopped) return
      this.ws = null
      if (!opened) this.neverOpened += 1
      if (this.neverOpened >= 3) {
        void this.checkAuth().then((bad) => {
          if (bad) {
            this.stopped = true
            this.sink?.state('auth_error')
          } else this.scheduleReconnect()
        })
        return
      }
      this.scheduleReconnect()
    }
  }

  // Browsers hide the HTTP status of a rejected WS handshake, so probe auth over plain HTTP.
  private async checkAuth(): Promise<boolean> {
    try {
      const res = await fetch(`${this.baseUrl}/api/runs?limit=1`, { headers: { Authorization: `Bearer ${this.token}` } })
      return res.status === 401 || res.status === 403
    } catch {
      return false
    }
  }

  private scheduleReconnect(): void {
    if (this.stopped) return
    const delay = BACKOFF[Math.min(this.attempts, BACKOFF.length - 1)]! * (0.8 + Math.random() * 0.4)
    this.attempts += 1
    this.sink?.state('reconnecting')
    this.timer = window.setTimeout(() => this.connect(), delay)
  }

  private onHello(ws: WebSocket, serverSeq: number): void {
    if (this.lastSeq === 0) {
      this.lastSeq = serverSeq
      this.sink?.resync()
      return
    }
    if (serverSeq < this.lastSeq) {
      this.lastSeq = serverSeq
      this.sink?.resync()
      return
    }
    if (serverSeq > this.lastSeq) {
      // Ring buffer holds 1000 events; a larger gap cannot be replayed faithfully.
      if (serverSeq - this.lastSeq > 900) {
        this.lastSeq = serverSeq
        this.sink?.resync()
      } else {
        ws.send(JSON.stringify({ op: 'resume', after_seq: this.lastSeq }))
      }
    }
  }
}
