<script setup lang="ts">
import { computed, nextTick, ref, useId, watch } from 'vue'
import { onClickOutside, useEventListener } from '@vueuse/core'
import { Boxes, ChevronDown, FolderDown, FolderOpen, LoaderCircle } from '@lucide/vue'
import type { BundleEngine, BundleOptions, EngineExportOptions } from '../../../preload/types'
import { useAssetFile } from '@/composables/assetFile'
import { usePresets } from '@/stores/catalog'

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
function engineOpts(): EngineExportOptions {
  const base = { ppu: ppuValid.value ? ppu.value : 100, pixel: pixelMode.value === 'auto' ? null : pixelMode.value === 'on' }
  if (!opts.value?.sprite_sheet) return base
  return { ...base, collider: colliderOn.value, collider_tolerance: tolValid.value ? tolerance.value : 1.5 }
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
            <p v-if="!ppuValid" class="b-err caption">PPU từ 1 đến 10000 — đang dùng 100</p>
          </div>
        </div>
      </Transition>
    </Teleport>
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
.b-err {
  color: var(--danger);
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
  .chev,
  .menu-enter-active,
  .menu-leave-active {
    transition: none;
  }
}
</style>
