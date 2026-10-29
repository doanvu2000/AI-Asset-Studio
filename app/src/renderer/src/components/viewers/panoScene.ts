import * as THREE from 'three'

export interface PanoScene {
  canvas: HTMLCanvasElement
  size: [number, number]
  /** Current view: yaw (° right of the panorama centre), pitch (° up), vertical field of view. */
  view(): { yaw: number; pitch: number; fov: number }
  setAutoRotate(on: boolean): void
  /** Eases the camera to a yaw/pitch; `reduced` jumps there. */
  lookAt(yaw: number, pitch: number, reduced: boolean): void
  dispose(): void
}

export interface PanoOptions {
  autoRotate: boolean
  signal: AbortSignal
  onView?: (v: { yaw: number; pitch: number; fov: number }) => void
}

const AUTO_DEG_PER_S = 6
const DAMPING = 0.9

function loadTexture(url: string): Promise<THREE.Texture> {
  return new Promise((resolve, reject) =>
    new THREE.TextureLoader().load(url, resolve, undefined, () => reject(new Error('Không tải được ảnh panorama')))
  )
}

export async function createPanoScene(host: HTMLElement, url: string, opts: PanoOptions): Promise<PanoScene> {
  const tex = await loadTexture(url)
  if (opts.signal.aborted) {
    tex.dispose()
    throw new DOMException('aborted', 'AbortError')
  }
  tex.colorSpace = THREE.SRGBColorSpace
  const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  renderer.outputColorSpace = THREE.SRGBColorSpace
  tex.anisotropy = renderer.capabilities.getMaxAnisotropy()
  const canvas = renderer.domElement
  canvas.style.display = 'block'
  canvas.style.touchAction = 'none'

  const scene = new THREE.Scene()
  const geo = new THREE.SphereGeometry(100, 96, 48)
  geo.scale(-1, 1, 1)
  const mat = new THREE.MeshBasicMaterial({ map: tex })
  const sphere = new THREE.Mesh(geo, mat)
  // SphereGeometry puts u = 0.5 at -X; this turns the panorama centre to the camera's default -Z.
  sphere.rotation.y = -Math.PI / 2
  scene.add(sphere)
  const camera = new THREE.PerspectiveCamera(75, 1, 0.1, 1000)

  let yaw = 0
  let pitch = 0
  let vYaw = 0
  let vPitch = 0
  let auto = opts.autoRotate
  let dragging = false
  let tween: { from: [number, number]; to: [number, number]; t0: number; ms: number } | null = null
  let last = performance.now()
  let lastX = 0
  let lastY = 0
  let dirty = true

  const degPerPx = (): number => camera.fov / Math.max(1, host.clientHeight)

  function onDown(e: PointerEvent): void {
    dragging = true
    tween = null
    vYaw = vPitch = 0
    lastX = e.clientX
    lastY = e.clientY
    canvas.setPointerCapture(e.pointerId)
  }
  function onMove(e: PointerEvent): void {
    if (!dragging) return
    const k = degPerPx()
    const dy = -(e.clientX - lastX) * k
    const dp = (e.clientY - lastY) * k
    yaw += dy
    pitch = THREE.MathUtils.clamp(pitch + dp, -89, 89)
    vYaw = dy
    vPitch = dp
    lastX = e.clientX
    lastY = e.clientY
    dirty = true
  }
  function onUp(e: PointerEvent): void {
    dragging = false
    if (canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId)
  }
  function onWheel(e: WheelEvent): void {
    e.preventDefault()
    camera.fov = THREE.MathUtils.clamp(camera.fov * Math.exp(e.deltaY * 0.001), 30, 110)
    camera.updateProjectionMatrix()
    dirty = true
  }
  canvas.addEventListener('pointerdown', onDown)
  canvas.addEventListener('pointermove', onMove)
  canvas.addEventListener('pointerup', onUp)
  canvas.addEventListener('pointercancel', onUp)
  canvas.addEventListener('wheel', onWheel, { passive: false })

  function resize(): void {
    const w = Math.max(1, host.clientWidth)
    const h = Math.max(1, host.clientHeight)
    renderer.setSize(w, h, false)
    camera.aspect = w / h
    camera.updateProjectionMatrix()
    dirty = true
  }
  const ro = new ResizeObserver(resize)
  ro.observe(host)
  resize()

  const target = new THREE.Vector3()
  renderer.setAnimationLoop((now) => {
    const dt = Math.min(0.1, (now - last) / 1000)
    last = now
    if (tween) {
      const t = Math.min(1, (now - tween.t0) / tween.ms)
      const e = 1 - Math.pow(1 - t, 3)
      yaw = tween.from[0] + (tween.to[0] - tween.from[0]) * e
      pitch = tween.from[1] + (tween.to[1] - tween.from[1]) * e
      if (t >= 1) tween = null
      dirty = true
    } else if (!dragging && (Math.abs(vYaw) > 0.01 || Math.abs(vPitch) > 0.01)) {
      yaw += vYaw
      pitch = THREE.MathUtils.clamp(pitch + vPitch, -89, 89)
      vYaw *= DAMPING
      vPitch *= DAMPING
      dirty = true
    } else if (!dragging && auto) {
      yaw += AUTO_DEG_PER_S * dt
      dirty = true
    }
    if (!dirty) return
    const y = THREE.MathUtils.degToRad(yaw)
    const p = THREE.MathUtils.degToRad(pitch)
    target.set(Math.sin(y) * Math.cos(p), Math.sin(p), -Math.cos(y) * Math.cos(p))
    camera.lookAt(target)
    renderer.render(scene, camera)
    dirty = false
    opts.onView?.({ yaw: ((yaw % 360) + 540) % 360 - 180, pitch, fov: camera.fov })
  })

  return {
    canvas,
    size: [(tex.image as HTMLImageElement | null)?.width ?? 0, (tex.image as HTMLImageElement | null)?.height ?? 0],
    view: () => ({ yaw, pitch, fov: camera.fov }),
    setAutoRotate(on) {
      auto = on
    },
    lookAt(toYaw, toPitch, reduced) {
      vYaw = vPitch = 0
      // Shortest way round, so "seam" from yaw 170 turns 10°, not 350°.
      const cur = ((yaw % 360) + 360) % 360
      const delta = ((toYaw - cur + 540) % 360) - 180
      const end: [number, number] = [yaw + delta, toPitch]
      if (reduced) {
        yaw = end[0]
        pitch = end[1]
        tween = null
        dirty = true
        return
      }
      tween = { from: [yaw, pitch], to: end, t0: performance.now(), ms: 650 }
    },
    dispose() {
      renderer.setAnimationLoop(null)
      ro.disconnect()
      canvas.removeEventListener('pointerdown', onDown)
      canvas.removeEventListener('pointermove', onMove)
      canvas.removeEventListener('pointerup', onUp)
      canvas.removeEventListener('pointercancel', onUp)
      canvas.removeEventListener('wheel', onWheel)
      geo.dispose()
      mat.dispose()
      tex.dispose()
      renderer.dispose()
      canvas.remove()
    }
  }
}
