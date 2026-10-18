import type { RendererLogLevel } from '../../../preload/types'

const MAX = 4000

function stringify(v: unknown): string {
  if (v instanceof Error) return v.stack ?? `${v.name}: ${v.message}`
  if (typeof v === 'string') return v
  try {
    return JSON.stringify(v) ?? String(v)
  } catch {
    return String(v)
  }
}

export function devlog(level: RendererLogLevel, ...parts: unknown[]): void {
  const msg = parts.map(stringify).join(' ')
  try {
    window.studio?.log(level, msg.length > MAX ? `${msg.slice(0, MAX)}…` : msg)
  } catch {
    /* bridge gone (window closing) */
  }
}

let installed = false

/** Mirrors console warnings/errors and uncaught failures into renderer.log (main redacts and rate-limits). */
export function installDevlog(): void {
  if (installed || !window.studio) return
  installed = true
  for (const level of ['warn', 'error'] as const) {
    const original = console[level].bind(console)
    console[level] = (...args: unknown[]) => {
      original(...args)
      devlog(level, ...args)
    }
  }
  window.addEventListener('error', (e) => {
    devlog('error', `uncaught: ${e.error ? stringify(e.error) : e.message} (${e.filename}:${e.lineno}:${e.colno})`)
  })
  window.addEventListener('unhandledrejection', (e) => devlog('error', 'unhandled rejection:', e.reason))
  devlog('info', `renderer start ${location.pathname}${location.hash}`)
}
