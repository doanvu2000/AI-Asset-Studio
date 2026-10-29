<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useEventListener } from '@vueuse/core'
import { FolderOpen, LayoutGrid, LoaderCircle, X } from '@lucide/vue'
import type { AtlasPageSize, AtlasResult, PackEngine } from '@/api/types'
import MobileStats from './MobileStats.vue'
import { api } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'
import { useFocusTrap } from '@/composables/focusTrap'

const props = defineProps<{ open: boolean; items: { step_id: string; name: string }[] }>()
const emit = defineEmits<{ close: [] }>()

const toasts = useToasts()
const root = ref<HTMLElement | null>(null)
const closeBtn = ref<HTMLButtonElement | null>(null)
const isOpen = computed(() => props.open)
const trap = useFocusTrap(root, isOpen, closeBtn)

type Target = PackEngine | 'json'
const TARGETS: { id: Target; label: string; hint: string }[] = [
  { id: 'unity', label: 'Unity', hint: 'Trang atlas Sprite Mode Multiple, rect + pivot từng sprite trong .meta' },
  { id: 'godot', label: 'Godot', hint: 'Mỗi sprite một AtlasTexture .tres (giữ kích thước gốc + pivot)' },
  { id: 'json', label: 'PNG + JSON', hint: 'Kiểu TexturePacker (JSON hash) cho engine khác; cho phép xoay 90°' }
]
const PAGE_SIZES: AtlasPageSize[] = [1024, 2048, 4096]

const name = ref('atlas')
const target = ref<Target>('unity')
const pageSize = ref<AtlasPageSize>(2048)
const padding = ref(2)
const pot = ref(true)
const trim = ref(true)
const rotate = ref(false)
const mobile = ref(false)
const state = ref<'idle' | 'building' | 'done'>('idle')
const result = ref<AtlasResult | null>(null)

const nameOk = computed(() => /^[\p{L}\p{N}_ -]{1,80}$/u.test(name.value.trim()))
const padOk = computed(() => Number.isInteger(padding.value) && padding.value >= 0 && padding.value <= 16)
const canBuild = computed(() => state.value !== 'building' && props.items.length > 0 && nameOk.value && padOk.value)

watch(
  () => props.open,
  (on) => {
    if (!on) return
    state.value = 'idle'
    result.value = null
  }
)

async function build(): Promise<void> {
  if (!canBuild.value) return
  state.value = 'building'
  result.value = null
  const engine = target.value === 'json' ? null : target.value
  try {
    result.value = await api().buildAtlas({
      steps: props.items.map((i) => ({ step_id: i.step_id, name: i.name })),
      name: name.value.trim(),
      engine,
      mobile: !!engine && mobile.value,
      atlas_max_size: pageSize.value,
      atlas_padding: padding.value,
      atlas_pot: pot.value,
      atlas_trim: trim.value,
      atlas_rotate: !engine && rotate.value
    })
    state.value = 'done'
    toasts.push(`Đã gộp ${result.value.stats.sprites} sprite vào ${result.value.stats.pages} trang atlas`, 'success')
  } catch (e) {
    state.value = 'idle'
    toasts.error(e, 'Không gộp được atlas')
  }
}

async function reveal(path: string): Promise<void> {
  try {
    if (!window.studio) throw new Error('Chỉ mở được thư mục trong ứng dụng desktop')
    await window.studio.revealPath(path)
  } catch (e) {
    toasts.error(e, 'Không mở được thư mục')
  }
}

useEventListener(
  document,
  'keydown',
  (e: KeyboardEvent) => {
    if (!props.open) return
    if (trap.onTab(e)) return
    if (e.key !== 'Escape') return
    e.preventDefault()
    e.stopPropagation()
    emit('close')
  },
  { capture: true }
)
</script>

<template>
  <Teleport to="body">
    <Transition name="dlg">
      <div v-if="open" ref="root" class="scrim" role="dialog" aria-modal="true" aria-labelledby="at-title" @click.self="emit('close')">
        <section class="sheet">
          <header class="head">
            <LayoutGrid class="ico" aria-hidden="true" />
            <div class="who">
              <h2 id="at-title" class="headline">Gộp atlas cho mobile</h2>
              <span class="caption">{{ items.length }} asset · bỏ viền trong suốt, chừa khoảng + kéo mép chống lem, pivot giữ nguyên</span>
            </div>
            <button ref="closeBtn" type="button" class="btn btn-plain btn-icon" aria-label="Đóng (Esc)" title="Đóng (Esc)" @click="emit('close')"><X /></button>
          </header>

          <div class="body">
            <div class="row">
              <span class="section-label">Xuất cho</span>
              <div class="seg" role="radiogroup" aria-label="Xuất cho">
                <button v-for="t in TARGETS" :key="t.id" type="button" role="radio" :aria-checked="target === t.id" :title="t.hint" @click="target = t.id">{{ t.label }}</button>
              </div>
            </div>
            <p class="caption hint">{{ TARGETS.find((t) => t.id === target)?.hint }}</p>
            <div class="row">
              <span class="section-label">Trang tối đa</span>
              <div class="seg" role="radiogroup" aria-label="Kích thước trang tối đa">
                <button v-for="n in PAGE_SIZES" :key="n" type="button" role="radio" :aria-checked="pageSize === n" @click="pageSize = n">{{ n }}</button>
              </div>
            </div>
            <div class="grid2">
              <label class="lbl">
                <span class="section-label">Tên atlas</span>
                <input v-model="name" class="field" :class="{ bad: !nameOk }" :aria-invalid="!nameOk" spellcheck="false" />
              </label>
              <label class="lbl">
                <span class="section-label">Khoảng cách (px)</span>
                <input v-model.number="padding" class="field num" :class="{ bad: !padOk }" :aria-invalid="!padOk" type="number" min="0" max="16" step="1" />
              </label>
            </div>
            <div class="checks">
              <label class="chk" title="Cạnh trang là lũy thừa của 2 (512, 1024…) — an toàn cho mọi GPU/định dạng nén"><input v-model="pot" type="checkbox" /> Cạnh lũy thừa 2</label>
              <label class="chk" title="Cắt viền trong suốt quanh từng sprite; pivot và kích thước gốc vẫn giữ"><input v-model="trim" type="checkbox" /> Bỏ viền trong suốt</label>
              <label v-if="target === 'json'" class="chk" title="Xoay 90° khi xếp vừa hơn — Unity/Godot không đọc được sprite xoay nên chỉ có ở PNG + JSON">
                <input v-model="rotate" type="checkbox" /> Cho xoay
              </label>
              <label v-else class="chk" title="Nén Android/iOS theo loại ảnh (ASTC/ETC2), giới hạn kích thước, tắt mipmap; kèm bảng RAM GPU trước/sau">
                <input v-model="mobile" type="checkbox" /> Ngân sách mobile
              </label>
            </div>

            <Transition name="fade" mode="out-in">
              <p v-if="state === 'building'" key="b" class="caption state" role="status"><LoaderCircle :size="14" class="spin" /> Đang xếp {{ items.length }} asset vào atlas…</p>
              <section v-else-if="result" key="r" class="card result" aria-live="polite">
                <MobileStats :atlas="result.stats" :budgets="result.mobile ? [result.mobile] : []" />
                <p v-if="result.skipped.length" class="caption">Không có sprite 2D: {{ result.skipped.join(', ') }}</p>
                <p class="caption mono ell" :title="result.dir">{{ result.dir }}</p>
                <div class="actions">
                  <button type="button" class="btn" @click="reveal(result.dir)"><FolderOpen /> Mở thư mục</button>
                </div>
              </section>
            </Transition>
          </div>

          <footer class="foot">
            <button type="button" class="btn btn-plain" @click="emit('close')">Đóng</button>
            <button type="button" class="btn btn-primary" :disabled="!canBuild" @click="build">
              <LoaderCircle v-if="state === 'building'" class="spin" /> <LayoutGrid v-else />
              {{ state === 'building' ? 'Đang gộp…' : state === 'done' ? 'Gộp lại' : 'Gộp atlas' }}
            </button>
          </footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(0, 0, 0, 0.32);
}
.sheet {
  width: min(560px, 100%);
  max-height: 100%;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  background: var(--bg-elevated);
  border-radius: var(--r-xl);
  box-shadow: var(--shadow-3);
  overflow: hidden;
}
.head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 10px 12px 16px;
  border-bottom: 0.5px solid var(--separator);
}
.ico {
  color: var(--accent);
  flex: none;
}
.who {
  flex: 1;
  min-width: 0;
  display: grid;
}
.body {
  overflow: auto;
  padding: 14px 16px;
  display: grid;
  gap: 10px;
  align-content: start;
}
.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.hint {
  margin-top: -4px;
}
.grid2 {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 10px;
}
.lbl {
  display: grid;
  gap: 4px;
  min-width: 0;
}
.field.bad {
  box-shadow: 0 0 0 1.5px var(--danger);
}
.checks {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
}
.chk {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}
.state {
  display: flex;
  align-items: center;
  gap: 6px;
}
.result {
  display: grid;
  gap: 8px;
  padding: 12px;
}
.result p {
  margin: 0;
}
.ell {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.actions {
  display: flex;
  gap: 8px;
}
.foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 16px;
  border-top: 0.5px solid var(--separator);
}
.dlg-enter-active,
.dlg-leave-active {
  transition: opacity var(--t-base) var(--ease-out);
}
.dlg-enter-active .sheet,
.dlg-leave-active .sheet {
  transition: transform var(--t-base) var(--ease-spring);
}
.dlg-enter-from,
.dlg-leave-to {
  opacity: 0;
}
.dlg-enter-from .sheet,
.dlg-leave-to .sheet {
  transform: scale(0.97) translateY(6px);
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity var(--t-fast) var(--ease-out);
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
