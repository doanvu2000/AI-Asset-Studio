<script setup lang="ts">
import { computed, nextTick, ref, useId, watch } from 'vue'
import { onClickOutside, useEventListener } from '@vueuse/core'
import { Boxes, ChevronDown, FolderDown, FolderOpen, LoaderCircle, Swords } from '@lucide/vue'
import type { BundleEngine, BundleOptions, EngineExportOptions, ShaderPresetInfo } from '../../../preload/types'
import { useAssetFile } from '@/composables/assetFile'
import { usePresets } from '@/stores/catalog'
import { hitExportOption, savedHit } from '@/composables/hitFx'
import HitFxDialog from './HitFxDialog.vue'

const props = defineProps<{ stepId: string; name?: string; compact?: boolean }>()

const file = useAssetFile()
const presets = usePresets()
const uid = useId()
const opts = ref<BundleOptions | null>(null)
const collider = ref<boolean | null>(null)
const open = ref(false)
const active = ref(0)
const button = ref<HTMLButtonElement | null>(null)
const menu = ref<HTMLElement | null>(null)
const pos = ref({ left: 0, top: 0 })

watch(
  () => props.stepId,
  async (id) => {
    opts.value = null
    collider.value = null
    if (!window.studio?.asset) return
    try {
      const o = await window.studio.asset.bundleOptions(id)
      if (id === props.stepId) opts.value = o
    } catch {
      /* not exportable as a folder (mock, unfinished step) → no button */
    }
    if (opts.value?.sprite_sheet) void presets.load()
  },
  { immediate: true }
)

const ENGINES: Record<BundleEngine, { label: string; hint: string }> = {
  unity: { label: 'Xuất cho Unity', hint: 'Sprite + anim + prefab' },
  godot: { label: 'Xuất cho Godot', hint: 'SpriteFrames + scene' }
}
const engines = computed(() => opts.value?.engines ?? [])

const PPU_KEY = 'export:ppu'
const PIXEL_KEY = 'export:pixel'
type PixelMode = 'auto' | 'on' | 'off'
function readPref(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}
function writePref(key: string, v: string): void {
  try {
    localStorage.setItem(key, v)
  } catch {
    /* storage blocked: the value just won't be remembered */
  }
}
const savedPpu = Number(readPref(PPU_KEY))
const ppu = ref(Number.isFinite(savedPpu) && savedPpu > 0 && savedPpu <= 10000 ? savedPpu : 100)
const savedPixel = readPref(PIXEL_KEY)
const pixelMode = ref<PixelMode>(savedPixel === 'on' || savedPixel === 'off' ? savedPixel : 'auto')
const ppuValid = computed(() => Number.isFinite(ppu.value) && ppu.value > 0 && ppu.value <= 10000)
watch(ppu, (v) => ppuValid.value && writePref(PPU_KEY, String(v)))
watch(pixelMode, (v) => writePref(PIXEL_KEY, v))
const TOL_KEY = 'export:collider-tol'
const colliderOn = computed(() => collider.value ?? !!opts.value?.collider_default)
const savedTol = Number(readPref(TOL_KEY) ?? NaN)
const tolerance = ref(Number.isFinite(savedTol) && savedTol >= 0 && savedTol <= 64 ? savedTol : 1.5)
const tolValid = computed(() => Number.isFinite(tolerance.value) && tolerance.value >= 0 && tolerance.value <= 64)
watch(tolerance, (v) => tolValid.value && writePref(TOL_KEY, String(v)))
const NORMAL_KEY = 'export:normal2d'
const NSTRENGTH_KEY = 'export:normal2d-strength'
const normal2d = ref(readPref(NORMAL_KEY) === 'on')
const savedStrength = Number(readPref(NSTRENGTH_KEY) ?? NaN)
const normalStrength = ref(Number.isFinite(savedStrength) && savedStrength > 0 && savedStrength <= 10 ? savedStrength : 1)
const strengthValid = computed(() => Number.isFinite(normalStrength.value) && normalStrength.value > 0 && normalStrength.value <= 10)
watch(normal2d, (v) => writePref(NORMAL_KEY, v ? 'on' : 'off'))
const ASE_KEY = 'export:aseprite'
const aseprite = ref(readPref(ASE_KEY) === 'on')
watch(aseprite, (v) => writePref(ASE_KEY, v ? 'on' : 'off'))
watch(normalStrength, (v) => strengthValid.value && writePref(NSTRENGTH_KEY, String(v)))
const SHADERS_KEY = 'export:shaders'
const shaderList = computed<ShaderPresetInfo[]>(() => opts.value?.shaders ?? [])
const picked = ref<string[]>(readJson<string[]>(SHADERS_KEY, []).filter((s) => typeof s === 'string'))
const shaderTweaks = ref<Record<string, { color?: string; speed?: number }>>({})
const pickedInfo = computed(() => shaderList.value.filter((s) => picked.value.includes(s.id)))
watch(picked, (v) => writePref(SHADERS_KEY, JSON.stringify(v)), { deep: true })
function readJson<T>(key: string, fallback: T): T {
  try {
    const v = JSON.parse(readPref(key) ?? 'null') as T | null
    return Array.isArray(fallback) === Array.isArray(v) && v !== null ? v : fallback
  } catch {
    return fallback
  }
}
function toggleShader(id: string): void {
  picked.value = picked.value.includes(id) ? picked.value.filter((s) => s !== id) : [...picked.value, id]
}
function tweak(id: string, key: 'color' | 'speed', v: string | number): void {
  shaderTweaks.value = { ...shaderTweaks.value, [id]: { ...shaderTweaks.value[id], [key]: v } }
}
const speedOk = (v: number | undefined): boolean => v === undefined || (Number.isFinite(v) && v >= 0 && v <= 20)
function shaderOpts(): Pick<EngineExportOptions, 'shaders' | 'shader_params'> {
  const ids = pickedInfo.value.map((s) => s.id)
  if (!ids.length) return {}
  const params: Record<string, Record<string, number | string>> = {}
  for (const id of ids) {
    const t = shaderTweaks.value[id]
    if (!t) continue
    params[id] = {}
    if (t.color) params[id].color = t.color
    if (t.speed !== undefined && speedOk(t.speed)) params[id].speed = t.speed
  }
  return { shaders: ids, shader_params: params }
}
// Off by default: Godot's VRAM import also changes desktop builds, and block compression smears UI / pixel art.
const MOBILE_KEY = 'export:mobile'
const mobile = ref(readPref(MOBILE_KEY) === 'on')
watch(mobile, (v) => writePref(MOBILE_KEY, v ? 'on' : 'off'))
function engineOpts(): EngineExportOptions {
  const hits = opts.value?.hits ? hitExportOption(props.stepId) : undefined
  const base = {
    ppu: ppuValid.value ? ppu.value : 100,
    pixel: pixelMode.value === 'auto' ? null : pixelMode.value === 'on',
    ...shaderOpts(),
    ...(hits ? { hits } : {}),
    ...(mobile.value ? { mobile: true } : {})
  }
  const normal = {
    ...(opts.value?.normal2d && normal2d.value ? { normal2d: true, normal2d_strength: strengthValid.value ? normalStrength.value : 1 } : {}),
    ...(opts.value?.aseprite && aseprite.value ? { aseprite: true } : {})
  }
  if (!opts.value?.sprite_sheet) return { ...base, ...normal }
  return { ...base, ...normal, collider: colliderOn.value, collider_tolerance: tolValid.value ? tolerance.value : 1.5 }
}
const hitOpen = ref(false)
const hitBound = computed(() => {
  const h = savedHit(props.stepId)
  return h?.enabled ? h.vfx_name || 'đã gắn' : null
})
function openHits(): void {
  close(false)
  hitOpen.value = true
}
const PIXEL_MODES: { id: PixelMode; label: string }[] = [
  { id: 'auto', label: 'Tự động' },
  { id: 'on', label: 'Bật' },
  { id: 'off', label: 'Tắt' }
]
const hasMenu = computed(() => !!opts.value?.sprite_sheet || engines.value.length > 0)
const visible = computed(() => !!opts.value && (hasMenu.value || opts.value.files > 1))
type Item = { id: string; label: string; hint?: string; preset?: string; engine?: BundleEngine; reveal?: boolean; sep?: boolean }
type Choice = { preset?: string; engine?: BundleEngine }
const last = ref<Choice | null>(null)
const items = computed<Item[]>(() => {
  const o = opts.value
  if (!o || !hasMenu.value) return []
  const list: Item[] = o.sprite_sheet
    ? presets.list.map((p) => ({ id: `p-${p.id}`, label: p.name, hint: p.id === o.preset ? 'Đang dùng' : undefined, preset: p.id }))
    : [{ id: 'raw', label: `Cả ${o.files} tệp` }]
  engines.value.forEach((e, i) => list.push({ id: `e-${e}`, label: ENGINES[e].label, hint: ENGINES[e].hint, engine: e, sep: i === 0 }))
  return [...list, { id: 'reveal', label: 'Mở thư mục đã xuất', reveal: true, sep: true }]
})
const itemId = (i: number): string => `${uid}-it-${i}`

async function show(): Promise<void> {
  const r = button.value?.getBoundingClientRect()
  if (!r) return
  pos.value = { left: Math.min(r.left, window.innerWidth - 288), top: r.bottom + 4 }
  active.value = 0
  open.value = true
  await nextTick()
  menu.value?.focus()
}

function close(refocus = true): void {
  if (!open.value) return
  open.value = false
  if (refocus) button.value?.focus()
}

function onButton(): void {
  if (!hasMenu.value) return void file.saveBundle(props.stepId, undefined, props.name)
  if (open.value) close()
  else void show()
}

function choose(i: number): void {
  const it = items.value[i]
  if (!it) return
  close()
  if (it.reveal) {
    const c = last.value ?? (opts.value?.sprite_sheet ? { preset: opts.value.preset ?? 'studio' } : {})
    return void file.revealBundle(props.stepId, c.preset, props.name, c.engine, c.engine ? engineOpts() : undefined)
  }
  last.value = { preset: it.preset, engine: it.engine }
  void file.saveBundle(props.stepId, it.preset, props.name, it.engine, it.engine ? engineOpts() : undefined)
}

function onKey(e: KeyboardEvent): void {
  if ((e.target as HTMLElement | null)?.closest('.b-opts') && e.key !== 'Escape' && e.key !== 'Tab') return
  const n = items.value.length
  const keys: Record<string, () => void> = {
    ArrowDown: () => (active.value = (active.value + 1) % n),
    ArrowUp: () => (active.value = (active.value - 1 + n) % n),
    Home: () => (active.value = 0),
    End: () => (active.value = n - 1),
    Enter: () => choose(active.value),
    ' ': () => choose(active.value),
    Escape: () => close()
  }
  if (e.key === 'Tab') return close(false)
  const fn = keys[e.key]
  if (!fn || !n) return
  e.preventDefault()
  e.stopPropagation()
  fn()
}

onClickOutside(menu, () => close(false), { ignore: [button] })
useEventListener(window, 'resize', () => close(false))
useEventListener(window, 'scroll', () => close(false), { capture: true, passive: true })
</script>

<template>
  <template v-if="visible">
    <button
      ref="button"
      type="button"
      class="btn btn-plain"
      :class="{ 'btn-icon': compact }"
      :disabled="!!file.busy.value"
      :aria-haspopup="hasMenu ? 'menu' : undefined"
      :aria-expanded="hasMenu ? open : undefined"
      :aria-label="compact ? 'Xuất thư mục' : undefined"
      :title="hasMenu ? 'Xuất cho engine: Unity, Godot hoặc sheet theo preset' : `Lưu cả ${opts?.files} tệp vào một thư mục`"
      @click="onButton"
    >
      <LoaderCircle v-if="file.busy.value === 'bundle'" class="spin" />
      <FolderDown v-else />
      <span v-if="!compact">{{ hasMenu ? 'Xuất cho engine' : 'Xuất thư mục…' }}</span>
      <ChevronDown v-if="hasMenu && !compact" :size="13" class="chev" :class="{ up: open }" aria-hidden="true" />
    </button>
    <Teleport to="body">
      <Transition name="menu">
        <div
          v-if="open"
          ref="menu"
          class="b-menu"
          role="menu"
          tabindex="-1"
          aria-label="Xuất cho engine"
          :aria-activedescendant="itemId(active)"
          :style="{ left: `${pos.left}px`, top: `${pos.top}px` }"
          @keydown="onKey"
        >
          <p class="b-head caption">{{ opts?.sprite_sheet ? 'Sheet + từng khung, thứ tự hướng theo engine' : 'Xuất thư mục' }}</p>
          <template v-for="(it, i) in items" :key="it.id">
            <hr v-if="it.sep" class="b-sep" />
            <div
              :id="itemId(i)"
              role="menuitem"
              class="b-item"
              :class="{ active: i === active }"
              @mousemove="active = i"
              @click="choose(i)"
            >
              <FolderOpen v-if="it.reveal" :size="14" aria-hidden="true" />
              <Boxes v-else-if="it.engine" :size="14" aria-hidden="true" />
              <span class="b-label">{{ it.reveal ? it.label : `${it.label}…` }}</span>
              <span v-if="it.hint" class="b-hint">{{ it.hint }}</span>
            </div>
          </template>
          <div v-if="engines.length" class="b-opts" @click.stop>
            <label class="b-opt">
              <span class="b-opt-label" title="Pixels per unit: số pixel ứng với 1 đơn vị thế giới trong Unity / Godot">PPU</span>
              <input
                v-model.number="ppu"
                class="field num b-ppu"
                :class="{ bad: !ppuValid }"
                type="number"
                min="1"
                max="10000"
                step="1"
                inputmode="numeric"
                :aria-invalid="!ppuValid"
                aria-label="Pixels per unit cho Unity / Godot"
              />
            </label>
            <div class="b-opt" role="radiogroup" aria-label="Lọc pixel (Point, không làm mờ)">
              <span class="b-opt-label" title="Lọc Point giữ nét pixel art; Tự động theo sheet">Lọc pixel</span>
              <div class="b-seg">
                <button
                  v-for="m in PIXEL_MODES"
                  :key="m.id"
                  type="button"
                  role="radio"
                  :aria-checked="pixelMode === m.id"
                  :class="{ on: pixelMode === m.id }"
                  @click="pixelMode = m.id"
                >
                  {{ m.label }}
                </button>
              </div>
            </div>
            <div v-if="opts?.sprite_sheet" class="b-opt">
              <label class="b-check" title="Vẽ polygon va chạm theo alpha từng clip (Unity Physics Shape, Godot CollisionPolygon2D) và đặt pivot ở chân">
                <input type="checkbox" :checked="colliderOn" @change="collider = ($event.target as HTMLInputElement).checked" />
                <span class="b-opt-label">Collider + pivot chân</span>
              </label>
              <input
                v-if="colliderOn"
                v-model.number="tolerance"
                class="field num b-ppu"
                :class="{ bad: !tolValid }"
                type="number"
                min="0"
                max="64"
                step="0.5"
                title="Độ sai lệch tối đa khi rút gọn viền (pixel): lớn hơn = ít đỉnh hơn"
                aria-label="Độ sai lệch viền collider (pixel)"
                :aria-invalid="!tolValid"
              />
            </div>
            <div v-if="opts?.normal2d" class="b-opt">
              <label class="b-check" title="Sinh normal map trên CPU từ alpha (vát tròn viền) + độ sáng, từng khung riêng — sprite nhận Light2D (Godot CanvasTexture, Unity URP 2D _NormalMap)">
                <input v-model="normal2d" type="checkbox" />
                <span class="b-opt-label">Normal map 2D</span>
              </label>
              <input
                v-if="normal2d"
                v-model.number="normalStrength"
                class="field num b-ppu"
                :class="{ bad: !strengthValid }"
                type="number"
                min="0.1"
                max="10"
                step="0.1"
                title="Độ nổi: lớn hơn = viền và chi tiết dốc hơn dưới đèn"
                aria-label="Độ nổi normal map 2D"
                :aria-invalid="!strengthValid"
              />
            </div>
            <div v-if="opts?.aseprite" class="b-opt">
              <label class="b-check" title="Thêm thư mục aseprite/: file .aseprite (mỗi clip × hướng là một tag, thời lượng khung theo fps, giữ layer) + sheet JSON array của Aseprite kèm PNG">
                <input v-model="aseprite" type="checkbox" />
                <span class="b-opt-label">Kèm file Aseprite</span>
              </label>
            </div>
            <div v-if="opts?.hits" class="b-opt">
              <span class="b-opt-label" title="Gắn VFX flipbook vào khung trúng đòn của clip tấn công: Unity AnimationEvent OnHit + script sinh VFX, Godot node nghe frame_changed, kèm hits.json">Hiệu ứng trúng đòn</span>
              <button type="button" class="btn btn-plain b-hit" :class="{ on: hitBound }" @click="openHits">
                <Swords :size="13" aria-hidden="true" />
                <span class="b-hit-name">{{ hitBound ?? 'Chọn…' }}</span>
              </button>
            </div>
            <div v-if="shaderList.length" class="b-shaders">
              <span class="b-opt-label" title="Shader viết tay, đã kiểm biên dịch trên Godot 4 và Unity URP 2D; mỗi preset kèm một material">Shader</span>
              <div class="b-chips" role="group" aria-label="Shader preset cho sprite">
                <button
                  v-for="s in shaderList"
                  :key="s.id"
                  type="button"
                  class="b-chip"
                  :class="{ on: picked.includes(s.id) }"
                  :aria-pressed="picked.includes(s.id)"
                  :title="s.hint"
                  @click="toggleShader(s.id)"
                >
                  {{ s.label }}
                </button>
              </div>
              <div v-for="s in pickedInfo" :key="s.id" class="b-opt b-shader">
                <span class="b-shader-name">{{ s.label }}</span>
                <input
                  v-if="s.color"
                  type="color"
                  class="b-color"
                  :value="shaderTweaks[s.id]?.color ?? s.color"
                  :aria-label="`Màu ${s.label}`"
                  @input="tweak(s.id, 'color', ($event.target as HTMLInputElement).value)"
                />
                <input
                  v-if="s.speed !== null"
                  class="field num b-speed"
                  :class="{ bad: !speedOk(shaderTweaks[s.id]?.speed) }"
                  type="number"
                  min="0"
                  max="20"
                  step="0.1"
                  :value="shaderTweaks[s.id]?.speed ?? s.speed"
                  :title="`Tốc độ (Hz), 0 = đứng yên — ${s.hint}`"
                  :aria-label="`Tốc độ ${s.label} (Hz)`"
                  :aria-invalid="!speedOk(shaderTweaks[s.id]?.speed)"
                  @input="tweak(s.id, 'speed', ($event.target as HTMLInputElement).valueAsNumber)"
                />
              </div>
              <p v-if="pickedInfo.length === 1 && opts?.sprite_sheet" class="b-note caption">Gán sẵn material vào sprite chính</p>
            </div>
            <div class="b-opt">
              <label
                class="b-check"
                title="Nén cho Android / iOS theo loại ảnh: Unity override ASTC (sprite 6×6, UI 4×4, pixel art không nén), Godot .import VRAM + ETC2/ASTC; giới hạn kích thước, tắt mipmap cho 2D. README kèm bảng RAM GPU trước/sau"
              >
                <input v-model="mobile" type="checkbox" />
                <span class="b-opt-label">Ngân sách mobile</span>
              </label>
            </div>
            <p v-if="!ppuValid" class="b-err caption">PPU từ 1 đến 10000 — đang dùng 100</p>
          </div>
        </div>
      </Transition>
    </Teleport>
    <HitFxDialog v-if="opts?.hits" v-model:open="hitOpen" :step-id="stepId" />
  </template>
</template>

<style scoped>
.chev {
  margin-left: -2px;
  color: var(--text-3);
  transition: rotate var(--t-fast) var(--ease-out);
}
.chev.up {
  rotate: 180deg;
}
.b-menu {
  position: fixed;
  z-index: 60;
  width: 280px;
  padding: 5px;
  border-radius: var(--r-md);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-2), var(--hairline);
  font-size: var(--fs-body);
  color: var(--text);
  outline: none;
  transform-origin: top left;
}
.b-head {
  margin: 2px 8px 4px;
  color: var(--text-3);
}
.b-sep {
  margin: 4px 6px;
  border: 0;
  border-top: 1px solid var(--separator);
}
.b-item {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 28px;
  padding: 3px 10px;
  border-radius: var(--r-sm);
  cursor: default;
}
.b-item.active {
  background: var(--accent);
  color: var(--text-on-accent);
}
.b-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.b-hint {
  font-size: var(--fs-caption);
  color: var(--text-3);
}
.b-item.active .b-hint {
  color: inherit;
  opacity: 0.85;
}
.b-opts {
  display: grid;
  gap: 6px;
  margin: 4px 2px 2px;
  padding: 8px 8px 6px;
  border-top: 1px solid var(--separator);
}
.b-opt {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.b-opt-label {
  font-size: var(--fs-callout);
  color: var(--text-2);
}
.b-check {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.b-ppu {
  width: 84px;
  height: 26px;
  text-align: right;
}
.b-ppu.bad {
  box-shadow: 0 0 0 1.5px var(--danger);
}
.b-seg {
  display: inline-flex;
  padding: 2px;
  border-radius: var(--r-sm);
  background: var(--bg-sunken);
}
.b-seg button {
  height: 22px;
  padding: 0 9px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--text-2);
  font: inherit;
  font-size: var(--fs-callout);
  transition: background-color var(--t-fast), color var(--t-fast);
}
.b-seg button.on {
  background: var(--bg-elevated);
  color: var(--text);
  box-shadow: var(--shadow-1);
}
.b-hit {
  height: 24px;
  max-width: 150px;
  padding: 0 8px;
  gap: 5px;
  font-size: var(--fs-caption);
}
.b-hit.on {
  color: var(--accent);
}
.b-hit-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.b-err {
  color: var(--danger);
}
.b-shaders {
  display: grid;
  gap: 6px;
}
.b-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.b-chip {
  height: 22px;
  padding: 0 8px;
  border: 0;
  border-radius: 11px;
  background: var(--bg-sunken);
  color: var(--text-2);
  font: inherit;
  font-size: var(--fs-caption);
  transition: background-color var(--t-fast), color var(--t-fast);
}
.b-chip.on {
  background: var(--accent);
  color: var(--text-on-accent);
}
.b-shader {
  justify-content: flex-start;
}
.b-shader-name {
  flex: 1;
  min-width: 0;
  font-size: var(--fs-caption);
  color: var(--text-2);
}
.b-color {
  width: 26px;
  height: 22px;
  padding: 0;
  border: 0;
  background: none;
}
.b-speed {
  width: 64px;
  height: 22px;
  text-align: right;
}
.b-speed.bad {
  box-shadow: 0 0 0 1.5px var(--danger);
}
.b-note {
  color: var(--text-3);
}
.spin {
  animation: spin 1s linear infinite;
}
.menu-enter-active {
  transition:
    opacity var(--t-fast) var(--ease-out),
    scale var(--t-fast) var(--ease-out);
}
.menu-leave-active {
  transition: opacity 90ms var(--ease-in);
}
.menu-enter-from {
  opacity: 0;
  scale: 0.97;
}
.menu-leave-to {
  opacity: 0;
}
@media (prefers-reduced-motion: reduce) {
  .b-chip,
  .chev,
  .menu-enter-active,
  .menu-leave-active {
    transition: none;
  }
}
</style>
