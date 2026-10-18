export interface ArtOptions {
  seed: number
  size?: number
  transparent?: boolean
  // 0 = final image, 1 = pure latent noise (used for fake sampler previews).
  noise?: number
}

function rng(seed: number): () => number {
  let s = seed >>> 0 || 1
  return () => {
    s ^= s << 13
    s ^= s >>> 17
    s ^= s << 5
    return ((s >>> 0) % 100000) / 100000
  }
}

function paint(ctx: OffscreenCanvasRenderingContext2D, n: number, o: ArtOptions): void {
  const r = rng(o.seed)
  const hue = Math.floor(r() * 360)
  if (!o.transparent) {
    ctx.fillStyle = '#f7f7f5'
    ctx.fillRect(0, 0, n, n)
  }
  const cx = n / 2
  const cy = n * 0.54
  ctx.save()
  ctx.fillStyle = 'rgba(0,0,0,0.12)'
  ctx.beginPath()
  ctx.ellipse(cx, n * 0.86, n * 0.24, n * 0.045, 0, 0, Math.PI * 2)
  ctx.fill()
  ctx.restore()

  const body = ctx.createLinearGradient(0, n * 0.2, 0, n * 0.85)
  body.addColorStop(0, `hsl(${hue} 70% 62%)`)
  body.addColorStop(1, `hsl(${(hue + 30) % 360} 60% 38%)`)
  ctx.fillStyle = body
  const shape = Math.floor(r() * 3)
  ctx.beginPath()
  if (shape === 0) {
    ctx.moveTo(cx, n * 0.14)
    ctx.lineTo(cx + n * 0.2, cy)
    ctx.lineTo(cx, n * 0.84)
    ctx.lineTo(cx - n * 0.2, cy)
  } else if (shape === 1) {
    ctx.ellipse(cx, cy, n * 0.26, n * 0.3, 0, 0, Math.PI * 2)
  } else {
    ctx.roundRect(cx - n * 0.22, n * 0.24, n * 0.44, n * 0.58, n * 0.08)
  }
  ctx.closePath()
  ctx.fill()

  ctx.fillStyle = `hsl(${(hue + 180) % 360} 80% 70% / 0.85)`
  for (let i = 0; i < 4; i++) {
    ctx.beginPath()
    ctx.arc(cx + (r() - 0.5) * n * 0.3, cy + (r() - 0.5) * n * 0.4, n * (0.02 + r() * 0.05), 0, Math.PI * 2)
    ctx.fill()
  }
  ctx.fillStyle = 'rgba(255,255,255,0.35)'
  ctx.beginPath()
  ctx.ellipse(cx - n * 0.07, n * 0.36, n * 0.05, n * 0.11, -0.4, 0, Math.PI * 2)
  ctx.fill()

  const noise = o.noise ?? 0
  if (noise > 0) {
    const img = ctx.getImageData(0, 0, n, n)
    const d = img.data
    for (let i = 0; i < d.length; i += 4) {
      const g = Math.random() * 255
      d[i] = d[i]! * (1 - noise) + g * noise
      d[i + 1] = d[i + 1]! * (1 - noise) + (g * 0.9) * noise
      d[i + 2] = d[i + 2]! * (1 - noise) + (g * 1.1) * noise
      d[i + 3] = 255
    }
    ctx.putImageData(img, 0, 0)
  }
}

export async function renderArt(o: ArtOptions): Promise<Blob> {
  const n = o.size ?? 512
  const canvas = new OffscreenCanvas(n, n)
  const ctx = canvas.getContext('2d')!
  paint(ctx, n, o)
  return canvas.convertToBlob({ type: o.transparent ? 'image/png' : 'image/webp', quality: 0.9 })
}

export async function renderPreviewB64(seed: number, noise: number): Promise<string> {
  const blob = await (async () => {
    const canvas = new OffscreenCanvas(192, 192)
    paint(canvas.getContext('2d')!, 192, { seed, noise })
    return canvas.convertToBlob({ type: 'image/jpeg', quality: 0.7 })
  })()
  const buf = new Uint8Array(await blob.arrayBuffer())
  let s = ''
  for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode(...buf.subarray(i, i + 0x8000))
  return btoa(s)
}
