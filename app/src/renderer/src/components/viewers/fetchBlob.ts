import { blobUrl } from '@/stores/connection'

// Blobs are content-addressed, so a hash always maps to the same bytes.
const texts = new Map<string, Promise<string>>()

export function blobText(hash: string): Promise<string> {
  let p = texts.get(hash)
  if (!p) {
    p = fetch(blobUrl(hash)).then((r) => {
      if (!r.ok) throw new Error(`Không tải được tệp (HTTP ${r.status})`)
      return r.text()
    })
    texts.set(hash, p)
    p.catch(() => texts.delete(hash))
  }
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
