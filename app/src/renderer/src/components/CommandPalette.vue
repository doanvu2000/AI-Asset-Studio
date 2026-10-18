<script setup lang="ts">
import { computed, nextTick, ref, watch, type Component } from 'vue'
import { useRouter } from 'vue-router'
import { onKeyStroke } from '@vueuse/core'
import { CornerDownLeft, Images, Inbox, Keyboard, Layers, LayoutDashboard, ListChecks, Plus, Search, Settings, Workflow } from '@lucide/vue'
import { useRecipes } from '@/stores/recipes'
import { useRuns } from '@/stores/runs'
import { RUN_LABEL } from '@/composables/format'
import { fold, openShortcuts, paletteOpen, shortcutsOpen } from '@/composables/palette'
import RecipeThumb from './RecipeThumb.vue'
import BlobImage from './BlobImage.vue'

const router = useRouter()
const recipes = useRecipes()
const runs = useRuns()

interface Item {
  id: string
  group: string
  label: string
  hint?: string
  icon?: Component
  recipeId?: string
  previewHash?: string | null
  run: () => void
}

const query = ref('')
const active = ref(0)
const inputEl = ref<HTMLInputElement | null>(null)
const listEl = ref<HTMLElement | null>(null)
let restoreFocus: HTMLElement | null = null

const go = (to: string) => () => void router.push(to)

const NAV: Item[] = [
  { id: 'nav-dash', group: 'Đi tới', label: 'Tổng quan', hint: 'Ctrl 1', icon: LayoutDashboard, run: go('/') },
  { id: 'nav-new', group: 'Đi tới', label: 'Tạo mới', hint: 'Ctrl N', icon: Plus, run: go('/new') },
  { id: 'nav-runs', group: 'Đi tới', label: 'Lượt chạy', hint: 'Ctrl 3', icon: ListChecks, run: go('/runs') },
  { id: 'nav-gates', group: 'Đi tới', label: 'Cần duyệt', hint: 'Ctrl 4', icon: Inbox, run: go('/gates') },
  { id: 'nav-gallery', group: 'Đi tới', label: 'Thư viện', hint: 'Ctrl 5', icon: Images, run: go('/gallery') },
  { id: 'nav-recipes', group: 'Đi tới', label: 'Quy trình', hint: 'Ctrl 6', icon: Workflow, run: go('/recipes') },
  { id: 'nav-batch', group: 'Đi tới', label: 'Hàng loạt', hint: 'Ctrl 7', icon: Layers, run: go('/batch') },
  { id: 'nav-batch-new', group: 'Đi tới', label: 'Tạo lô nhiều prompt', icon: Layers, run: go('/new?mode=batch') },
  { id: 'nav-settings', group: 'Đi tới', label: 'Cài đặt', icon: Settings, run: go('/settings') },
  { id: 'nav-keys', group: 'Đi tới', label: 'Phím tắt', hint: '?', icon: Keyboard, run: () => openShortcuts() }
]

const items = computed<Item[]>(() => {
  const q = fold(query.value.trim())
  const match = (s: string) => !q || fold(s).includes(q)
  const nav = NAV.filter((i) => match(i.label))
  const make = recipes.list
    .filter((r) => match(r.name) || match(`tạo ${r.name}`))
    .slice(0, q ? 8 : 5)
    .map<Item>((r) => ({
      id: `recipe-${r.id}`,
      group: 'Tạo mới theo quy trình',
      label: r.name,
      recipeId: r.id,
      run: () => void router.push({ path: '/new', query: { recipe: r.id } })
    }))
  const recent = runs.runList
    .filter((r) => match(r.title || '') || match(recipes.byId(r.recipe_id)?.name ?? ''))
    .slice(0, q ? 8 : 5)
    .map<Item>((r) => ({
      id: `run-${r.id}`,
      group: 'Lượt chạy gần đây',
      label: r.title || recipes.byId(r.recipe_id)?.name || 'Lượt chạy',
      hint: RUN_LABEL[r.status],
      recipeId: r.recipe_id,
      previewHash: runs.previewHash(r.id),
      run: () => void router.push(`/runs/${r.id}`)
    }))
  return q ? [...make, ...recent, ...nav] : [...recent, ...make, ...nav]
})

const groups = computed(() => {
  const out: Array<{ name: string; items: Array<Item & { index: number }> }> = []
  items.value.forEach((it, index) => {
    let g = out.find((x) => x.name === it.group)
    if (!g) out.push((g = { name: it.group, items: [] }))
    g.items.push({ ...it, index })
  })
  return out
})

watch(query, () => (active.value = 0))
watch(paletteOpen, async (open) => {
  if (open) {
    restoreFocus = document.activeElement as HTMLElement | null
    query.value = ''
    active.value = 0
    void recipes.load()
    await nextTick()
    inputEl.value?.focus()
  } else restoreFocus?.focus?.()
})
watch(shortcutsOpen, (open) => {
  if (open) restoreFocus = document.activeElement as HTMLElement | null
  else restoreFocus?.focus?.()
})

function choose(i: number): void {
  const it = items.value[i]
  if (!it) return
  paletteOpen.value = false
  it.run()
}

function move(delta: number): void {
  const n = items.value.length
  if (!n) return
  active.value = (active.value + delta + n) % n
  void nextTick(() => listEl.value?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' }))
}

function onInputKey(e: KeyboardEvent): void {
  if (e.key === 'ArrowDown') move(1)
  else if (e.key === 'ArrowUp') move(-1)
  else if (e.key === 'Enter') choose(active.value)
  else if (e.key === 'Escape') paletteOpen.value = false
  else return
  e.preventDefault()
}

function typing(e: KeyboardEvent): boolean {
  const t = e.target as HTMLElement | null
  return !!t && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName))
}

onKeyStroke(
  (e) => (e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === 'k',
  (e) => {
    e.preventDefault()
    shortcutsOpen.value = false
    paletteOpen.value = !paletteOpen.value
  }
)
onKeyStroke('?', (e) => {
  if (typing(e) || paletteOpen.value || e.ctrlKey || e.metaKey || (!shortcutsOpen.value && document.querySelector('[role=dialog],[role=alertdialog]'))) return
  e.preventDefault()
  shortcutsOpen.value = !shortcutsOpen.value
})
onKeyStroke('Escape', () => {
  shortcutsOpen.value = false
  paletteOpen.value = false
})

const SHORTCUTS: Array<{ group: string; rows: Array<{ keys: string[]; label: string }> }> = [
  {
    group: 'Chung',
    rows: [
      { keys: ['Ctrl', 'K'], label: 'Tìm nhanh: đi tới, tạo mới, mở lượt chạy' },
      { keys: ['Ctrl', 'N'], label: 'Tạo mới' },
      { keys: ['Ctrl', '1 … 7'], label: 'Tổng quan · Tạo mới · Lượt chạy · Cần duyệt · Thư viện · Quy trình · Hàng loạt' },
      { keys: ['?'], label: 'Bảng phím tắt này' }
    ]
  },
  { group: 'Tạo mới', rows: [{ keys: ['Ctrl', 'Enter'], label: 'Bắt đầu chạy' }] },
  {
    group: 'Cần duyệt',
    rows: [
      { keys: ['1 … 9'], label: 'Chọn ảnh theo số' },
      { keys: ['←', '→'], label: 'Mục trước / sau' },
      { keys: ['Enter'], label: 'Duyệt' },
      { keys: ['X'], label: 'Từ chối' }
    ]
  }
]
</script>

<template>
  <Teleport to="body">
    <Transition name="pal">
      <div v-if="paletteOpen" class="scrim" @mousedown.self="paletteOpen = false">
        <div class="palette" role="dialog" aria-modal="true" aria-label="Tìm nhanh">
          <div class="search">
            <Search :size="16" class="s-ic" aria-hidden="true" />
            <input
              ref="inputEl"
              v-model="query"
              class="s-input"
              type="text"
              placeholder="Tìm màn, quy trình hoặc lượt chạy…"
              role="combobox"
              aria-expanded="true"
              aria-controls="pal-list"
              :aria-activedescendant="items[active] ? `pal-${items[active]!.id}` : undefined"
              @keydown="onInputKey"
            />
            <span class="kbd">Esc</span>
          </div>
          <div id="pal-list" ref="listEl" class="list" role="listbox" aria-label="Kết quả">
            <p v-if="!items.length" class="none caption">Không tìm thấy “{{ query }}”.</p>
            <div v-for="g in groups" :key="g.name" class="grp" role="group" :aria-label="g.name">
              <p class="section-label g-label">{{ g.name }}</p>
              <div
                v-for="it in g.items"
                :id="`pal-${it.id}`"
                :key="it.id"
                class="opt"
                role="option"
                :aria-selected="it.index === active"
                @mousemove="active = it.index"
                @click="choose(it.index)"
              >
                <span class="o-ic" aria-hidden="true">
                  <span v-if="it.previewHash" class="o-thumb"><BlobImage :hash="it.previewHash" :size="64" /></span>
                  <RecipeThumb v-else-if="it.recipeId" :id="it.recipeId" :size="24" />
                  <component :is="it.icon" v-else-if="it.icon" :size="16" />
                </span>
                <span class="o-label">{{ it.label }}</span>
                <span v-if="it.hint" class="o-hint caption">{{ it.hint }}</span>
                <CornerDownLeft v-if="it.index === active" :size="14" class="o-enter" aria-hidden="true" />
              </div>
            </div>
          </div>
          <footer class="foot caption">
            <span><span class="kbd">↑</span> <span class="kbd">↓</span> chọn</span>
            <span><span class="kbd">Enter</span> mở</span>
            <span><span class="kbd">?</span> phím tắt</span>
          </footer>
        </div>
      </div>
    </Transition>

    <Transition name="pal">
      <div v-if="shortcutsOpen" class="scrim" @mousedown.self="shortcutsOpen = false">
        <div class="palette sheet" role="dialog" aria-modal="true" aria-labelledby="keys-title">
          <header class="sheet-head">
            <h2 id="keys-title" class="title-2">Phím tắt</h2>
            <button class="btn btn-plain" autofocus @click="shortcutsOpen = false">Đóng</button>
          </header>
          <div class="sheet-body">
            <section v-for="g in SHORTCUTS" :key="g.group" class="k-grp">
              <h3 class="section-label">{{ g.group }}</h3>
              <div v-for="r in g.rows" :key="r.label" class="k-row">
                <span class="k-keys">
                  <span v-for="k in r.keys" :key="k" class="kbd">{{ k }}</span>
                </span>
                <span class="k-label">{{ r.label }}</span>
              </div>
            </section>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: 12vh 16px 16px;
  background: var(--scrim);
}
.palette {
  width: min(600px, 100%);
  max-height: 70vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-radius: var(--r-xl);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-3), var(--hairline);
}
.search {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 14px;
  height: 52px;
  border-bottom: 0.5px solid var(--separator);
}
.s-ic {
  color: var(--text-3);
  flex: none;
}
.s-input {
  flex: 1;
  min-width: 0;
  height: 100%;
  border: 0;
  background: transparent;
  font-size: 15px;
  user-select: text;
}
.s-input:focus-visible {
  outline: none;
}
.s-input::placeholder {
  color: var(--text-3);
}
.list {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 6px;
}
.none {
  padding: 18px 12px;
}
.g-label {
  padding: 10px 10px 4px;
}
.opt {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 38px;
  padding: 0 10px;
  border-radius: var(--r-sm);
  cursor: pointer;
}
.opt[aria-selected='true'] {
  background: var(--bg-selected);
}
.o-ic {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  flex: none;
  color: var(--accent);
}
.o-thumb {
  width: 26px;
  height: 26px;
  border-radius: 6px;
  overflow: hidden;
  box-shadow: var(--hairline);
}
.o-thumb :deep(.blob) {
  width: 100%;
  height: 100%;
}
.o-label {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.o-enter {
  color: var(--text-3);
}
.foot {
  display: flex;
  gap: 16px;
  padding: 8px 14px;
  border-top: 0.5px solid var(--separator);
}
.sheet {
  width: min(560px, 100%);
}
.sheet-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 12px 12px 18px;
  border-bottom: 0.5px solid var(--separator);
}
.sheet-body {
  overflow: auto;
  padding: 8px 18px 18px;
  display: grid;
  gap: 14px;
}
.k-grp {
  display: grid;
  gap: 6px;
  padding-top: 8px;
}
.k-row {
  display: grid;
  grid-template-columns: 130px 1fr;
  align-items: center;
  gap: 12px;
  min-height: 26px;
}
.k-keys {
  display: inline-flex;
  gap: 4px;
}
.k-label {
  color: var(--text-2);
}
.pal-enter-active {
  transition: opacity var(--t-fast) var(--ease-out);
}
.pal-enter-active .palette {
  transition: transform var(--t-base) var(--ease-spring);
}
.pal-leave-active {
  transition: opacity 120ms var(--ease-in);
}
.pal-enter-from,
.pal-leave-to {
  opacity: 0;
}
.pal-enter-from .palette {
  transform: translateY(-8px) scale(0.98);
}
</style>
