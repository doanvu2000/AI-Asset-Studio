import * as THREE from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js'

export interface GlbStats {
  triangles: number
  vertices: number
  materials: number
  textures: number
  meshes: number
  size: [number, number, number]
}

export interface GlbScene {
  canvas: HTMLCanvasElement
  stats: GlbStats
  setAutoRotate(on: boolean): void
  setWireframe(on: boolean): void
  setGrid(on: boolean): void
  setDark(dark: boolean): void
  resetView(): void
  dispose(): void
}

export interface GlbOptions {
  dark: boolean
  autoRotate: boolean
  signal: AbortSignal
  onProgress?: (fraction: number | null) => void
}

const GRID = { light: [0xb8b8c2, 0xdcdce2], dark: [0x4a4a52, 0x34343a] } as const

function collectStats(root: THREE.Object3D, box: THREE.Box3): GlbStats {
  let triangles = 0
  let vertices = 0
  let meshes = 0
  const materials = new Set<THREE.Material>()
  const textures = new Set<THREE.Texture>()
  root.traverse((o) => {
    const mesh = o as THREE.Mesh
    if (!mesh.isMesh) return
    meshes += 1
    const g = mesh.geometry
    const pos = g.getAttribute('position')
    vertices += pos?.count ?? 0
    triangles += Math.floor((g.index?.count ?? pos?.count ?? 0) / 3)
    for (const m of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
      materials.add(m)
      for (const v of Object.values(m)) if ((v as THREE.Texture | null)?.isTexture) textures.add(v as THREE.Texture)
    }
  })
  const s = box.getSize(new THREE.Vector3())
  return { triangles, vertices, materials: materials.size, textures: textures.size, meshes, size: [s.x, s.y, s.z] }
}

function load(url: string, onProgress?: (f: number | null) => void): Promise<THREE.Group> {
  return new Promise((resolve, reject) => {
    new GLTFLoader().load(
      url,
      (gltf) => resolve(gltf.scene),
      (e) => onProgress?.(e.lengthComputable && e.total ? e.loaded / e.total : null),
      (err) => reject(err instanceof Error ? err : new Error(String(err)))
    )
  })
}

export async function createGlbScene(host: HTMLElement, url: string, opts: GlbOptions): Promise<GlbScene> {
  const model = await load(url, opts.onProgress)
  if (opts.signal.aborted) throw new DOMException('aborted', 'AbortError')

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.shadowMap.enabled = true
  renderer.shadowMap.type = THREE.PCFShadowMap
  const canvas = renderer.domElement
  canvas.style.display = 'block'

  const scene = new THREE.Scene()
  const pmrem = new THREE.PMREMGenerator(renderer)
  const room = new RoomEnvironment()
  const env = pmrem.fromScene(room, 0.04).texture
  scene.environment = env
  room.dispose()

  model.traverse((o) => {
    const mesh = o as THREE.Mesh
    if (mesh.isMesh) mesh.castShadow = mesh.receiveShadow = true
  })
  scene.add(model)
  const box = new THREE.Box3().setFromObject(model)
  const sphere = box.getBoundingSphere(new THREE.Sphere())
  const radius = Math.max(sphere.radius, 1e-3)
  const center = sphere.center
  const floorY = box.min.y

  const key = new THREE.DirectionalLight(0xffffff, 1.4)
  key.position.set(center.x + radius * 2, floorY + radius * 4, center.z + radius * 2.5)
  key.target.position.copy(center)
  key.castShadow = true
  key.shadow.mapSize.set(1024, 1024)
  key.shadow.bias = -0.0005
  const sc = key.shadow.camera
  sc.left = sc.bottom = -radius * 1.6
  sc.right = sc.top = radius * 1.6
  sc.near = radius * 0.5
  sc.far = radius * 10
  scene.add(key, key.target)

  const ground = new THREE.Mesh(new THREE.PlaneGeometry(radius * 12, radius * 12), new THREE.ShadowMaterial({ opacity: 0.18 }))
  ground.rotation.x = -Math.PI / 2
  ground.position.set(center.x, floorY, center.z)
  ground.receiveShadow = true
  scene.add(ground)

  let grid: THREE.GridHelper | null = null
  let dark = opts.dark
  function buildGrid(): void {
    const show = grid?.visible ?? true
    if (grid) {
      scene.remove(grid)
      grid.geometry.dispose()
      ;(grid.material as THREE.Material).dispose()
    }
    const [c1, c2] = dark ? GRID.dark : GRID.light
    const step = 10 ** Math.floor(Math.log10(radius))
    const size = Math.ceil((radius * 6) / step) * step
    grid = new THREE.GridHelper(size, Math.round(size / (step / 2)), c1, c2)
    grid.position.set(center.x, floorY + radius * 1e-4, center.z)
    const mat = grid.material as THREE.LineBasicMaterial
    mat.transparent = true
    mat.opacity = 0.7
    mat.depthWrite = false
    grid.visible = show
    scene.add(grid)
  }
  buildGrid()

  const camera = new THREE.PerspectiveCamera(35, 1, radius / 100, radius * 100)
  const controls = new OrbitControls(camera, canvas)
  controls.enableDamping = true
  controls.dampingFactor = 0.08
  controls.autoRotate = opts.autoRotate
  controls.autoRotateSpeed = 1.6
  controls.minDistance = radius * 0.4
  controls.maxDistance = radius * 12
  controls.maxPolarAngle = Math.PI * 0.495

  let dirty = true
  function resetView(): void {
    const dist = radius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) * 1.05
    const dir = new THREE.Vector3(0.9, 0.55, 1.25).normalize()
    camera.position.copy(center).addScaledVector(dir, dist)
    controls.target.copy(center)
    controls.update()
    dirty = true
  }

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
  controls.addEventListener('change', () => (dirty = true))

  resize()
  resetView()
  // Idle frames skip rendering: only damping/auto-rotate movement or a setting change redraws.
  renderer.setAnimationLoop(() => {
    const moved = controls.update()
    if (moved || dirty) {
      renderer.render(scene, camera)
      dirty = false
    }
  })

  const wireOriginal = new Map<THREE.Material, boolean>()
  return {
    canvas,
    stats: collectStats(model, box),
    setAutoRotate(on) {
      controls.autoRotate = on
      dirty = true
    },
    setWireframe(on) {
      model.traverse((o) => {
        const mesh = o as THREE.Mesh
        if (!mesh.isMesh) return
        for (const m of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
          const wm = m as THREE.MeshStandardMaterial
          if (!wireOriginal.has(m)) wireOriginal.set(m, wm.wireframe)
          wm.wireframe = on || !!wireOriginal.get(m)
        }
      })
      dirty = true
    },
    setGrid(on) {
      if (grid) grid.visible = on
      dirty = true
    },
    setDark(d) {
      dark = d
      buildGrid()
      dirty = true
    },
    resetView,
    dispose() {
      renderer.setAnimationLoop(null)
      ro.disconnect()
      controls.dispose()
      scene.traverse((o) => {
        const mesh = o as THREE.Mesh
        if (!mesh.isMesh && !(o as THREE.LineSegments).isLineSegments) return
        mesh.geometry?.dispose()
        for (const m of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
          for (const v of Object.values(m)) if ((v as THREE.Texture | null)?.isTexture) (v as THREE.Texture).dispose()
          m.dispose()
        }
      })
      env.dispose()
      pmrem.dispose()
      renderer.dispose()
      // Chromium caps live WebGL contexts (~16); release ours now instead of waiting for GC.
      renderer.forceContextLoss()
      canvas.remove()
    }
  }
}
