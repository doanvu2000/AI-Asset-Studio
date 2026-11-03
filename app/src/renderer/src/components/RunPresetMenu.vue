<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import { onClickOutside, useEventListener } from '@vueuse/core'
import { Bookmark, BookmarkPlus, ChevronDown, LoaderCircle, Trash2, X } from '@lucide/vue'
import { runPresetsApi, type RunPreset } from '@/api/runPresets'
import RelTime from './RelTime.vue'
import { useConnection } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'
import { useFocusTrap } from '@/composables/focusTrap'

const props = defineProps<{
  recipeId: string
  /** Real project id, or undefined when the run belongs to no project. */
  projectId?: string
  projectName?: string
  snapshot: () => { inputs: Record<string, unknown>; params: Record<string, unknown> }
}>()
const emit = defineEmits<{ apply: [preset: RunPreset] }>()

const toasts = useToasts()
const conn = useConnection()
const uid = useId()
const root = ref<HTMLElement | null>(null)
const open = ref(false)
const list = ref<RunPreset[]>([])
const loading = ref(false)
const failed = ref(false)
const confirmId = ref<string | null>(null)

let seq = 0
async function load(): Promise<void> {
  if (!conn.transport) return
  const mine = ++seq
  loading.value = true
  try {
    const out = await runPresetsApi().listRunPresets(props.recipeId, props.projectId)
    if (mine === seq) {
      list.value = out
      failed.value = false
    }
  } catch {
    if (mine === seq) failed.value = true
  } finally {
    if (mine === seq) loading.value = false
  }
}
watch(() => [props.recipeId, props.projectId, conn.transport], load, { immediate: true })

onClickOutside(root, () => (open.value = false))
useEventListener(document, 'keydown', (e: KeyboardEvent) => {
  if (e.key === 'Escape' && open.value && !dialogOpen.value) {
    e.stopPropagation()
    open.value = false
  }
})

function pick(p: RunPreset): void {
  open.value = false
  emit('apply', p)
}

async function remove(p: RunPreset): Promise<void> {
  if (confirmId.value !== p.id) {
    confirmId.value = p.id
    return
  }
  confirmId.value = null
  try {
    await runPresetsApi().deleteRunPreset(p.id)
    list.value = list.value.filter((x) => x.id !== p.id)
    toasts.push(`Đã xoá thiết lập “${p.name}”`, 'success', 2200)
  } catch (e) {
    toasts.error(e, 'Không xoá được thiết lập')
  }
}

const dialog = ref<HTMLElement | null>(null)
const nameEl = ref<HTMLInputElement | null>(null)
const dialogOpen = ref(false)
const name = ref('')
const projectOnly = ref(false)
const busy = ref(false)
const trap = useFocusTrap(dialog, dialogOpen, nameEl)
const trimmed = computed(() => name.value.trim())
const clash = computed(() => list.value.some((p) => p.name.toLowerCase() === trimmed.value.toLowerCase()))

function openSave(): void {
  open.value = false
  name.value = ''
  projectOnly.value = false
  dialogOpen.value = true
}
function closeSave(): void {
  dialogOpen.value = false
}
useEventListener(
  document,
  'keydown',
  (e: KeyboardEvent) => {
    if (!dialogOpen.value) return
    if (trap.onTab(e)) return
    if (e.key !== 'Escape') return
    e.preventDefault()
    e.stopPropagation()
    closeSave()
  },
  { capture: true }
)

async function save(): Promise<void> {
  if (!trimmed.value || busy.value) return
  busy.value = true
  try {
    const { inputs, params } = props.snapshot()
    const p = await runPresetsApi().saveRunPreset({
      name: trimmed.value,
      recipe_id: props.recipeId,
      project_id: projectOnly.value ? (props.projectId ?? null) : null,
      inputs,
      params
    })
    list.value = [p, ...list.value]
    toasts.push(`Đã lưu thiết lập “${p.name}”`, 'success', 2200)
    closeSave()
  } catch (e) {
    toasts.error(e, 'Không lưu được thiết lập')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div ref="root" class="rp">
    <button
      type="button"
      class="btn rp-btn"
      :aria-expanded="open"
      :aria-controls="`${uid}-menu`"
      aria-haspopup="true"
      title="Điền form từ một thiết lập đã lưu, hoặc lưu thiết lập hiện tại"
      @click="open = !open"
    >
      <Bookmark :size="14" aria-hidden="true" />
      Thiết lập đã lưu
      <span v-if="list.length" class="rp-count num">{{ list.length }}</span>
      <ChevronDown :size="14" class="rp-chev" :class="{ up: open }" aria-hidden="true" />
    </button>

    <Transition name="rp-pop">
      <div v-if="open" :id="`${uid}-menu`" class="rp-menu" role="group" aria-label="Thiết lập đã lưu">
        <p v-if="loading && !list.length" class="rp-note caption" role="status"><LoaderCircle :size="13" class="spin" /> Đang tải…</p>
        <p v-else-if="failed" class="rp-note caption err" role="alert">
          Không tải được danh sách. <button type="button" class="rp-link" @click="load">Thử lại</button>
        </p>
        <p v-else-if="!list.length" class="rp-note caption">Chưa có thiết lập nào cho quy trình này. Điền form rồi bấm “Lưu thiết lập hiện tại”.</p>
        <ul v-else class="rp-list">
          <li v-for="p in list" :key="p.id" class="rp-item">
            <button type="button" class="rp-pick" @click="pick(p)">
              <span class="rp-name">{{ p.name }}</span>
              <span class="rp-meta caption">
                {{ p.project_id ? `Chỉ dự án ${projectName ?? 'này'}` : 'Mọi dự án' }} · <RelTime :ts="p.created_at" />
              </span>
            </button>
            <button
              type="button"
              class="rp-del"
              :class="{ ask: confirmId === p.id }"
              :aria-label="confirmId === p.id ? `Bấm lần nữa để xoá ${p.name}` : `Xoá ${p.name}`"
              @click="remove(p)"
              @blur="confirmId === p.id && (confirmId = null)"
            >
              <Trash2 :size="14" aria-hidden="true" />
              <span v-if="confirmId === p.id">Xoá?</span>
            </button>
          </li>
        </ul>
        <button type="button" class="rp-save" @click="openSave"><BookmarkPlus :size="15" aria-hidden="true" /> Lưu thiết lập hiện tại…</button>
      </div>
    </Transition>

    <Teleport to="body">
      <Transition name="rp-dlg">
        <div v-if="dialogOpen" ref="dialog" class="scrim" role="dialog" aria-modal="true" :aria-labelledby="`${uid}-t`" @click.self="closeSave">
          <form class="sheet" @submit.prevent="save">
            <header class="d-head">
              <BookmarkPlus class="d-ico" :size="20" aria-hidden="true" />
              <h2 :id="`${uid}-t`" class="headline">Lưu thiết lập này</h2>
              <button type="button" class="btn btn-plain btn-icon" aria-label="Đóng (Esc)" title="Đóng (Esc)" @click="closeSave"><X /></button>
            </header>
            <div class="d-body">
              <label class="d-label" :for="`${uid}-n`">Tên thiết lập</label>
              <input :id="`${uid}-n`" ref="nameEl" v-model="name" class="field" type="text" maxlength="80" autocomplete="off" placeholder="Ví dụ: Rương gỗ, nền tối" />
              <p v-if="clash" class="caption warn">Đã có thiết lập trùng tên. Lưu sẽ tạo thêm một bản mới.</p>
              <label v-if="projectId" class="d-chk">
                <input v-model="projectOnly" type="checkbox" /> Chỉ dùng cho dự án <strong>{{ projectName }}</strong>
              </label>
              <p class="caption d-hint">Lưu các giá trị đang điền, cả file đã thêm. Không lưu danh sách nhiều lượt.</p>
            </div>
            <footer class="d-foot">
              <button type="button" class="btn" @click="closeSave">Huỷ</button>
              <button type="submit" class="btn btn-primary" :disabled="!trimmed || busy">
                <LoaderCircle v-if="busy" class="spin" :size="14" /> Lưu
              </button>
            </footer>
          </form>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.rp {
  position: relative;
}
.rp-btn {
  gap: 6px;
}
.rp-count {
  min-width: 18px;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: var(--fs-caption);
  font-weight: 600;
  text-align: center;
}
.rp-chev {
  margin-left: -2px;
  color: var(--text-3);
  transition: rotate var(--t-fast) var(--ease-out);
}
.rp-chev.up {
  rotate: 180deg;
}
.rp-menu {
  position: absolute;
  z-index: 40;
  top: calc(100% + 6px);
  right: 0;
  width: min(340px, calc(100vw - 32px));
  padding: 5px;
  border-radius: var(--r-md);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-2), var(--hairline);
  transform-origin: top right;
}
.rp-note {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 6px 8px;
  color: var(--text-2);
}
.rp-link {
  border: 0;
  padding: 0;
  background: none;
  color: var(--accent);
  font: inherit;
  cursor: pointer;
}
.rp-list {
  display: grid;
  margin: 0;
  padding: 0 0 4px;
  max-height: 260px;
  overflow-y: auto;
  list-style: none;
  border-bottom: 1px solid var(--separator);
}
.rp-item {
  display: flex;
  align-items: stretch;
  gap: 2px;
}
.rp-pick {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 1px;
  padding: 6px 10px;
  border: 0;
  border-radius: var(--r-sm);
  background: none;
  color: var(--text);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.rp-pick:hover,
.rp-pick:focus-visible {
  background: var(--bg-hover);
}
.rp-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}
.rp-meta {
  color: var(--text-3);
}
.rp-del {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0 8px;
  border: 0;
  border-radius: var(--r-sm);
  background: none;
  color: var(--text-3);
  font: inherit;
  font-size: var(--fs-caption);
  cursor: pointer;
}
.rp-del:hover,
.rp-del:focus-visible {
  color: var(--st-fail);
  background: var(--bg-hover);
}
.rp-del.ask {
  color: var(--st-fail);
  font-weight: 600;
}
.rp-save {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  margin-top: 4px;
  padding: 7px 10px;
  border: 0;
  border-radius: var(--r-sm);
  background: none;
  color: var(--accent);
  font: inherit;
  font-weight: 500;
  text-align: left;
  cursor: pointer;
}
.rp-save:hover,
.rp-save:focus-visible {
  background: var(--accent-soft);
}
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
  width: min(420px, 100%);
  display: grid;
  background: var(--bg-elevated);
  border-radius: var(--r-xl);
  box-shadow: var(--shadow-3);
  overflow: hidden;
}
.d-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 10px 12px 16px;
  border-bottom: 1px solid var(--hair-soft);
}
.d-head h2 {
  flex: 1;
  margin: 0;
}
.d-ico {
  color: var(--accent);
  flex: none;
}
.d-body {
  display: grid;
  gap: 8px;
  padding: var(--sp-4) var(--sp-5);
}
.d-label {
  font-weight: 500;
}
.d-chk {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}
.d-hint {
  color: var(--text-3);
}
.d-foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 16px 14px;
}
.spin {
  animation: spin 1s linear infinite;
}
.rp-pop-enter-active,
.rp-pop-leave-active {
  transition:
    opacity var(--t-fast) var(--ease-out),
    transform var(--t-base) var(--ease-spring);
}
.rp-pop-enter-from,
.rp-pop-leave-to {
  opacity: 0;
  transform: scale(0.96) translateY(-4px);
}
.rp-dlg-enter-active,
.rp-dlg-leave-active {
  transition: opacity var(--t-base) var(--ease-out);
}
.rp-dlg-enter-active .sheet {
  transition: transform var(--t-slow) var(--ease-spring);
}
.rp-dlg-leave-active .sheet {
  transition: transform var(--t-fast) var(--ease-in);
}
.rp-dlg-enter-from,
.rp-dlg-leave-to {
  opacity: 0;
}
.rp-dlg-enter-from .sheet,
.rp-dlg-leave-to .sheet {
  transform: scale(0.97);
}
@media (prefers-reduced-motion: reduce) {
  .rp-pop-enter-active,
  .rp-pop-leave-active,
  .rp-dlg-enter-active .sheet,
  .rp-dlg-leave-active .sheet,
  .rp-chev {
    transition: none;
  }
}
</style>
