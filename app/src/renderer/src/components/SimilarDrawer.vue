<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useEventListener } from '@vueuse/core'
import { ImagePlus, LoaderCircle, Search, Sparkles, X } from '@lucide/vue'
import type { SimilarHit, SimilarResult } from '@/api/types'
import BlobImage from './BlobImage.vue'
import { api } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'
import { useFocusTrap } from '@/composables/focusTrap'
import { MORE_LIKE_HINT, moreLikeQuery, scoreText } from '@/composables/similar'

const props = defineProps<{ open: boolean; hash: string; versionId?: string; projectId?: string; name?: string }>()
const emit = defineEmits<{ close: [] }>()

const router = useRouter()
const toasts = useToasts()
const root = ref<HTMLElement | null>(null)
const closeBtn = ref<HTMLButtonElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const isOpen = computed(() => props.open)
const trap = useFocusTrap(root, isOpen, closeBtn)

const result = ref<SimilarResult | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const inProject = ref(false)
const text = ref('')
const label = ref('')
const making = ref(false)
let seq = 0

const scope = computed(() => (inProject.value && props.projectId && props.projectId !== 'default' ? props.projectId : undefined))

async function run(fn: () => Promise<SimilarResult>, what: string): Promise<void> {
  const id = ++seq
  loading.value = true
  error.value = null
  label.value = what
  try {
    const r = await fn()
    if (id === seq) result.value = r
  } catch (e) {
    if (id === seq) error.value = (e as Error).message
  } finally {
    if (id === seq) loading.value = false
  }
}

const byAsset = (): Promise<void> =>
  run(() => api().similarAssets(props.versionId ? { version_id: props.versionId } : { blob: props.hash }, { k: 24, project_id: scope.value }), props.name ?? 'asset này')

function byText(): void {
  const t = text.value.trim()
  if (t) void run(() => api().similarSearch({ text: t }, { k: 24, project_id: scope.value }), `“${t}”`)
}

function byFile(e: Event): void {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (f) void run(() => api().similarSearch({ file: f }, { k: 24, project_id: scope.value }), f.name)
  if (fileInput.value) fileInput.value.value = ''
}

watch(
  () => [props.open, props.hash, inProject.value] as const,
  ([on]) => {
    if (on) {
      text.value = ''
      void byAsset()
    }
  },
  { immediate: true }
)

async function makeMore(): Promise<void> {
  if (making.value) return
  making.value = true
  try {
    const plan = await api().moreLike(props.versionId ? { version_id: props.versionId } : { blob: props.hash })
    emit('close')
    toasts.push(`Mở “${plan.recipe_name}”: ${MORE_LIKE_HINT[plan.mode]}`, 'success', 3200)
    await router.push({ path: '/new', query: moreLikeQuery(plan, props.projectId) })
  } catch (e) {
    toasts.error(e, 'Không mở được quy trình tạo thêm')
  } finally {
    making.value = false
  }
}

function openHit(h: SimilarHit): void {
  emit('close')
  void router.push(`/runs/${h.run_id}`)
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
    <Transition name="sd">
      <div v-if="open" ref="root" class="scrim" role="dialog" aria-modal="true" aria-labelledby="sd-title" @click.self="emit('close')">
        <aside class="drawer">
          <header class="head">
            <BlobImage :hash="hash" :size="128" class="src" :alt="name ?? ''" />
            <div class="who">
              <h2 id="sd-title" class="headline">Asset giống</h2>
              <span class="caption ell">Giống {{ label || name || 'asset này' }}</span>
            </div>
            <button ref="closeBtn" type="button" class="btn btn-plain btn-icon" aria-label="Đóng (Esc)" title="Đóng (Esc)" @click="emit('close')"><X /></button>
          </header>

          <div class="tools">
            <button type="button" class="btn btn-primary make" :disabled="making" title="Mở quy trình đã tạo ra asset này, điền sẵn đầu vào, dùng nó làm style mẫu" @click="makeMore">
              <LoaderCircle v-if="making" class="spin" /> <Sparkles v-else /> Tạo thêm giống cái này
            </button>
            <form class="query" role="search" @submit.prevent="byText">
              <Search :size="14" class="q-ico" aria-hidden="true" />
              <input v-model="text" type="search" placeholder="Hoặc tả bằng chữ: rương gỗ, bình máu…" aria-label="Tìm asset theo mô tả" />
              <button type="button" class="btn btn-plain btn-icon" title="Tìm theo một ảnh khác" aria-label="Tìm theo một ảnh khác" @click="fileInput?.click()"><ImagePlus /></button>
              <input ref="fileInput" type="file" accept="image/*" hidden @change="byFile" />
            </form>
            <label v-if="projectId && projectId !== 'default'" class="opt caption"><input v-model="inProject" type="checkbox" /> Chỉ trong dự án này</label>
          </div>

          <p v-if="result?.mode === 'phash'" class="note caption">Chưa cài model CLIP nên đang so theo dấu vân ảnh (pHash): chỉ bắt được ảnh gần như trùng, không tìm theo chữ.</p>
          <p v-if="result?.pending" class="note caption" role="status"><LoaderCircle :size="12" class="spin" /> Còn {{ result.pending }} ảnh đang lập chỉ mục, mở lại sau để thấy đủ.</p>

          <div class="body" :aria-busy="loading">
            <p v-if="loading && !result" class="caption state" role="status"><LoaderCircle :size="14" class="spin" /> Đang so ảnh trong thư viện…</p>
            <p v-else-if="error" class="caption state err">{{ error }} <button type="button" class="btn btn-plain" @click="byAsset">Thử lại</button></p>
            <p v-else-if="result && !result.results.length" class="caption state">Thư viện chưa có asset nào khác để so.</p>
            <ul v-else-if="result" v-auto-animate class="grid" :class="{ dim: loading }">
              <li v-for="h in result.results" :key="h.version_id">
                <button type="button" class="hit" :title="`${h.asset_name} · mở lượt chạy`" @click="openHit(h)">
                  <BlobImage :hash="h.blob.hash" :size="256" :alt="h.asset_name" class="thumb" />
                  <span class="match" :style="{ '--m': Math.max(0, Math.min(1, h.score)) }" aria-hidden="true" />
                  <span class="cap">
                    <span class="nm ell">{{ h.asset_name }}</span>
                    <span class="sc num">{{ scoreText(h.score, result.mode) }}</span>
                  </span>
                  <span v-if="h.status === 'approved'" class="ok caption">Đã duyệt</span>
                </button>
              </li>
            </ul>
          </div>
        </aside>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: flex;
  justify-content: flex-end;
  background: rgba(0, 0, 0, 0.32);
}
.drawer {
  width: min(560px, 100%);
  height: 100%;
  display: grid;
  grid-template-rows: auto auto auto auto minmax(0, 1fr);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-3);
}
.head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 10px 12px 16px;
  border-bottom: 0.5px solid var(--separator);
}
.src {
  width: 44px;
  height: 44px;
  flex: none;
  border-radius: 8px;
  overflow: hidden;
  background: var(--bg-sunken);
}
.who {
  flex: 1;
  min-width: 0;
  display: grid;
}
.ell {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tools {
  display: grid;
  gap: 8px;
  padding: 12px 16px 4px;
}
.make {
  justify-self: start;
}
.query {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 0 4px 0 10px;
  border-radius: var(--r-sm);
  background: var(--bg-sunken);
  box-shadow: inset 0 0 0 1px var(--separator);
}
.query:focus-within {
  box-shadow: inset 0 0 0 1.5px var(--accent);
}
.q-ico {
  color: var(--text-3);
  flex: none;
}
.query input[type='search'] {
  flex: 1;
  min-width: 0;
  height: 30px;
  border: 0;
  background: transparent;
  color: var(--text);
  font: inherit;
  outline: none;
}
.opt {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  cursor: pointer;
}
.note {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 6px 16px 0;
  color: var(--text-2);
}
.body {
  overflow-y: auto;
  padding: 10px 16px 20px;
}
.state {
  display: flex;
  align-items: center;
  gap: 6px;
}
.err {
  color: var(--st-fail);
}
.grid {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 10px;
  transition: opacity var(--t-base);
}
.grid.dim {
  opacity: 0.5;
}
.hit {
  position: relative;
  width: 100%;
  display: grid;
  gap: 0;
  padding: 0;
  border: 0;
  border-radius: var(--r-md);
  overflow: hidden;
  background: var(--bg-sunken);
  color: inherit;
  text-align: left;
  cursor: pointer;
  box-shadow: var(--hairline);
  transition:
    transform var(--t-fast) var(--ease-out),
    box-shadow var(--t-fast);
}
.hit:hover {
  transform: translateY(-1px);
  box-shadow: var(--shadow-2), var(--hairline);
}
.hit:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.thumb {
  aspect-ratio: 1;
  width: 100%;
}
/* Match strength as a bar along the thumbnail's bottom edge: scan the grid by length, read the % for detail. */
.match {
  height: 3px;
  background: linear-gradient(90deg, var(--accent) calc(var(--m) * 100%), transparent 0);
  opacity: 0.85;
}
.cap {
  display: flex;
  align-items: baseline;
  gap: 6px;
  padding: 5px 8px 7px;
  background: var(--bg-elevated);
}
.nm {
  flex: 1;
  min-width: 0;
  font-size: var(--fs-callout);
}
.sc {
  font-size: var(--fs-caption);
  color: var(--text-2);
  font-variant-numeric: tabular-nums;
}
.ok {
  position: absolute;
  top: 6px;
  left: 6px;
  padding: 0 6px;
  border-radius: 999px;
  color: var(--st-ok);
  background: var(--st-ok-soft);
}
.spin {
  animation: spin 1s linear infinite;
}
.sd-enter-active,
.sd-leave-active {
  transition: opacity var(--t-base) var(--ease-out);
}
.sd-enter-active .drawer {
  transition: transform var(--t-slow) var(--ease-spring);
}
.sd-leave-active .drawer {
  transition: transform var(--t-fast) var(--ease-in);
}
.sd-enter-from,
.sd-leave-to {
  opacity: 0;
}
.sd-enter-from .drawer,
.sd-leave-to .drawer {
  transform: translateX(32px);
}
@media (prefers-reduced-motion: reduce) {
  .sd-enter-active .drawer,
  .sd-leave-active .drawer,
  .hit {
    transition: none;
  }
  .sd-enter-from .drawer,
  .sd-leave-to .drawer {
    transform: none;
  }
  .spin {
    animation: none;
  }
}
</style>
