import { blobUrl } from '@/stores/connection'

// Blobs are content-addressed, so a hash always maps to the same bytes; the cap keeps big Spine JSON from piling up.
const texts = new Map<string, Promise<string>>()
const CACHE_MAX = 48
const FETCH_TIMEOUT_MS = 30_000

export function blobText(hash: string): Promise<string> {
  let p = texts.get(hash)
  if (p) {
    texts.delete(hash)
    texts.set(hash, p)
    return p
  }
  p = fetch(blobUrl(hash), { signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) }).then(
    (r) => {
      if (!r.ok) throw new Error(`Không tải được tệp (HTTP ${r.status})`)
      return r.text()
    },
    (e: Error) => {
      throw new Error(e.name === 'TimeoutError' ? 'Tải tệp quá lâu, thử lại sau' : `Không tải được tệp: ${e.message}`)
    }
  )
  texts.set(hash, p)
  p.catch(() => {
    if (texts.get(hash) === p) texts.delete(hash)
  })
  while (texts.size > CACHE_MAX) texts.delete(texts.keys().next().value!)
  return p
}

export async function blobJson<T>(hash: string): Promise<T> {
  return JSON.parse(await blobText(hash)) as T
}

export function toDataUri(text: string, mime: string): string {
  const bytes = new TextEncoder().encode(text)
  let s = ''
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return `data:${mime};base64,${btoa(s)}`
}
