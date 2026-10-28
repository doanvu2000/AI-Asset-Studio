<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useEventListener } from '@vueuse/core'
import { FileArchive, FolderOpen, LoaderCircle, Package, RotateCcw, TriangleAlert, X } from '@lucide/vue'
import type { PackCommercial, PackEngine, PackLicense, PackListing, PackResult } from '@/api/types'
import BlobImage from './BlobImage.vue'
import RelTime from './RelTime.vue'
import { api } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'
import { useFocusTrap } from '@/composables/focusTrap'

const props = defineProps<{ open: boolean; projectId: string }>()
const emit = defineEmits<{ close: [] }>()

const toasts = useToasts()
const root = ref<HTMLElement | null>(null)
const closeBtn = ref<HTMLButtonElement | null>(null)
const isOpen = computed(() => props.open)
const trap = useFocusTrap(root, isOpen, closeBtn)

const ENGINES: { id: 'unity' | 'godot' | 'both'; label: string }[] = [
  { id: 'unity', label: 'Unity' },
  { id: 'godot', label: 'Godot' },
  { id: 'both', label: 'Cả hai' }
]
const LICENSES: { id: PackLicense; label: string; hint: string }[] = [
  { id: 'cc0', label: 'CC0', hint: 'Tặng công chúng, ai cũng dùng được, không cần ghi công' },
  { id: 'cc-by-4.0', label: 'CC BY 4.0', hint: 'Dùng tự do kể cả thương mại, phải ghi công tác giả' },
  { id: 'proprietary', label: 'Độc quyền', hint: 'Bản mẫu “All rights reserved”, thay bằng điều khoản của bạn trước khi phát hành' }
]
const COMMERCIAL: Record<PackCommercial, string> = {
  yes: 'Dùng thương mại được',
  restricted: 'Có điều kiện',
  unknown: 'Chưa xác minh',
  no: 'Không dùng thương mại'
}

const listing = ref<PackListing | null>(null)
const loading = ref(false)
const loadError = ref<string | null>(null)
const engine = ref<'unity' | 'godot' | 'both'>('unity')
const license = ref<PackLicense>('cc0')
const version = ref('')
const author = ref('')
const org = ref('assetstudio')
const outDir = ref<string | null>(null)
const picked = ref<Set<string>>(new Set())
const building = ref(false)
const result = ref<PackResult | null>(null)

const assets = computed(() => listing.value?.assets ?? [])
const allPicked = computed(() => assets.value.length > 0 && assets.value.every((a) => picked.value.has(a.step_id)))
const engines = computed<PackEngine[]>(() => (engine.value === 'both' ? ['unity', 'godot'] : [engine.value]))
const versionOk = computed(() => !version.value.trim() || /^\d+\.\d+\.\d+$/.test(version.value.trim()))
const canBuild = computed(() => !building.value && picked.value.size > 0 && versionOk.value)
const flagged = computed(() => (result.value ? result.value.items.filter((i) => i.commercial !== 'yes') : []))

async function load(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    listing.value = await api().projectPacks(props.projectId)
    picked.value = new Set(listing.value.assets.map((a) => a.step_id))
  } catch (e) {
    loadError.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

watch(
  () => props.open,
  (on) => {
    if (!on) return
    result.value = null
    void load()
  },
  { immediate: true }
)

function toggle(stepId: string): void {
  const next = new Set(picked.value)
  if (next.has(stepId)) next.delete(stepId)
  else next.add(stepId)
  picked.value = next
}

function toggleAll(): void {
  picked.value = allPicked.value ? new Set() : new Set(assets.value.map((a) => a.step_id))
}

async function chooseDir(): Promise<void> {
  const dir = await window.studio?.pickPath({ kind: 'dir', title: 'Thư mục lưu asset pack', defaultPath: outDir.value ?? listing.value?.default_dir })
  if (dir) outDir.value = dir
}

async function reveal(path: string | null | undefined): Promise<void> {
  if (!path) return
  try {
    if (!window.studio) throw new Error('Chỉ mở được thư mục trong ứng dụng desktop')
    await window.studio.revealPath(path)
  } catch (e) {
    toasts.error(e, 'Không mở được thư mục')
  }
}

async function build(): Promise<void> {
  if (!canBuild.value) return
  building.value = true
  try {
    const all = allPicked.value
    const include = all ? undefined : assets.value.filter((a) => picked.value.has(a.step_id)).flatMap((a) => a.version_ids)
    result.value = await api().buildPack(props.projectId, {
      engines: engines.value,
      license: license.value,
      version: version.value.trim() || undefined,
      include,
      author: author.value.trim() || undefined,
      org: org.value.trim() || undefined,
      out_dir: outDir.value ?? undefined
    })
    version.value = ''
    toasts.push(`Đã đóng gói bản ${result.value.version}`, 'success')
    void load()
  } catch (e) {
    toasts.error(e, 'Không đóng gói được')
  } finally {
    building.value = false
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
    <Transition name="pk">
      <div v-if="open" ref="root" class="scrim" role="dialog" aria-modal="true" aria-labelledby="pk-title" @click.self="emit('close')">
        <section class="sheet">
          <header class="head">
            <Package class="ico" aria-hidden="true" />
            <div class="who">
              <h2 id="pk-title" class="headline">Đóng gói asset pack</h2>
              <span class="caption">Chỉ ghi ra ổ đĩa máy này — không đẩy lên Git hay dịch vụ nào.</span>
            </div>
            <button ref="closeBtn" type="button" class="btn btn-plain btn-icon" aria-label="Đóng (Esc)" title="Đóng (Esc)" @click="emit('close')"><X /></button>
          </header>

          <div class="body">
            <p v-if="loading && !listing" class="caption state" role="status"><LoaderCircle :size="14" class="spin" /> Đang tải danh sách asset đã duyệt…</p>
            <p v-else-if="loadError" class="caption state err">{{ loadError }} <button type="button" class="btn btn-plain" @click="load">Thử lại</button></p>
            <template v-else-if="listing">
              <div class="row">
                <span class="section-label">Engine</span>
                <div class="seg" role="radiogroup" aria-label="Engine">
                  <button v-for="e in ENGINES" :key="e.id" type="button" role="radio" :aria-checked="engine === e.id" @click="engine = e.id">{{ e.label }}</button>
                </div>
              </div>
              <div class="row">
                <span class="section-label">Giấy phép</span>
                <div class="seg" role="radiogroup" aria-label="Giấy phép">
                  <button v-for="l in LICENSES" :key="l.id" type="button" role="radio" :aria-checked="license === l.id" :title="l.hint" @click="license = l.id">{{ l.label }}</button>
                </div>
              </div>
              <p class="caption hint">{{ LICENSES.find((l) => l.id === license)?.hint }}</p>

              <div class="grid2">
                <label class="lbl">
                  <span class="section-label">Phiên bản</span>
                  <input v-model="version" class="field num" :class="{ bad: !versionOk }" :placeholder="`${listing.next_version} (tự tăng)`" spellcheck="false" />
                </label>
                <label class="lbl">
                  <span class="section-label">Tác giả</span>
                  <input v-model="author" class="field" placeholder="Tên studio / tác giả" />
                </label>
                <label class="lbl">
                  <span class="section-label">Tổ chức (tên gói Unity)</span>
                  <input v-model="org" class="field" placeholder="assetstudio" spellcheck="false" />
                </label>
                <div class="lbl">
                  <span class="section-label">Thư mục lưu</span>
                  <div class="dir">
                    <span class="caption ell" :title="outDir ?? listing.default_dir">{{ outDir ?? listing.default_dir }}</span>
                    <button type="button" class="btn btn-plain" @click="chooseDir"><FolderOpen /> Chọn</button>
                    <button v-if="outDir" type="button" class="btn btn-plain btn-icon" title="Về thư mục mặc định" aria-label="Về thư mục mặc định" @click="outDir = null"><RotateCcw /></button>
                  </div>
                </div>
              </div>

              <div class="row">
                <span class="section-label">Asset ({{ picked.size }}/{{ assets.length }})</span>
                <button v-if="assets.length" type="button" class="btn btn-plain" @click="toggleAll">{{ allPicked ? 'Bỏ chọn hết' : 'Chọn hết' }}</button>
              </div>
              <p v-if="!assets.length" class="caption state">Dự án chưa có asset nào được duyệt. Duyệt phiên bản trong Thư viện trước khi đóng gói.</p>
              <ul v-else class="list">
                <li v-for="a in assets" :key="a.step_id">
                  <label class="item">
                    <input type="checkbox" :checked="picked.has(a.step_id)" @change="toggle(a.step_id)" />
                    <BlobImage v-if="a.preview_hash" :hash="a.preview_hash" :size="64" class="thumb" :alt="a.name" />
                    <span v-else class="thumb" />
                    <span class="nm ell">{{ a.name }}</span>
                    <span class="caption">{{ a.engine_ready ? a.recipe_id : 'file gốc' }}</span>
                  </label>
                </li>
              </ul>

              <section v-if="result" class="card result" aria-live="polite">
                <p class="headline-sm">Bản {{ result.version }}<span v-if="result.previous" class="caption"> (trước: {{ result.previous }})</span></p>
                <p class="caption mono ell" :title="result.dir">{{ result.dir }}</p>
                <p class="caption">
                  Thêm {{ result.changes.added.length }} · Cập nhật {{ result.changes.updated.length }} · Bỏ {{ result.changes.removed.length }}
                </p>
                <div v-if="flagged.length" class="warn">
                  <TriangleAlert :size="14" aria-hidden="true" />
                  <span>Kiểm tra trước khi dùng thương mại: {{ flagged.map((f) => `${f.name} (${COMMERCIAL[f.commercial]})`).join(', ') }}. Chi tiết model + giấy phép trong provenance.json.</span>
                </div>
                <p v-for="w in result.warnings" :key="w" class="caption">{{ w }}</p>
                <div class="actions">
                  <button type="button" class="btn" @click="reveal(result.dir)"><FolderOpen /> Mở thư mục</button>
                  <button v-if="result.zip" type="button" class="btn btn-plain" @click="reveal(result.zip)"><FileArchive /> Hiện file zip</button>
                </div>
              </section>

              <details v-if="listing.packs.length" class="history">
                <summary class="section-label">Đã đóng gói ({{ listing.packs.length }})</summary>
                <ul>
                  <li v-for="p in listing.packs" :key="p.version">
                    <span class="num">{{ p.version }}</span>
                    <span class="caption"><RelTime :ts="p.created_at" /> · {{ p.engines.join(' + ') }}</span>
                    <button type="button" class="btn btn-plain btn-icon" title="Mở thư mục" aria-label="Mở thư mục" @click="reveal(p.dir)"><FolderOpen /></button>
                  </li>
                </ul>
              </details>
            </template>
          </div>

          <footer class="foot">
            <button type="button" class="btn btn-plain" @click="emit('close')">Đóng</button>
            <button type="button" class="btn btn-primary" :disabled="!canBuild" @click="build">
              <LoaderCircle v-if="building" class="spin" /> <Package v-else />
              {{ building ? 'Đang đóng gói…' : `Đóng gói ${version.trim() || listing?.next_version || ''}` }}
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
  width: min(620px, 100%);
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
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
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
.dir {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.dir .ell {
  flex: 1;
  min-width: 0;
}
.ell {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 240px;
  overflow: auto;
  border-radius: var(--r-md);
  background: var(--bg-group);
  box-shadow: var(--hairline);
}
.item {
  display: grid;
  grid-template-columns: auto 32px minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  padding: 6px 10px;
  cursor: pointer;
  transition: background var(--t-fast) var(--ease-out);
}
.item:hover {
  background: var(--bg-hover);
}
li + li .item {
  border-top: 0.5px solid var(--separator);
}
.thumb {
  width: 32px;
  height: 32px;
  border-radius: 6px;
  overflow: hidden;
  background: var(--bg-sunken);
}
.result {
  padding: 12px 14px;
  display: grid;
  gap: 4px;
}
.headline-sm {
  margin: 0;
  font-weight: 600;
}
.result p {
  margin: 0;
}
.mono {
  font-family: var(--mono);
}
.warn {
  display: flex;
  gap: 6px;
  align-items: flex-start;
  padding: 8px 10px;
  border-radius: var(--r-md);
  background: var(--st-human-soft);
  color: var(--text);
  font-size: var(--fs-caption);
}
.warn svg {
  flex: none;
  color: var(--st-human);
  margin-top: 1px;
}
.actions {
  display: flex;
  gap: 8px;
  margin-top: 6px;
}
.history ul {
  list-style: none;
  margin: 6px 0 0;
  padding: 0;
  display: grid;
  gap: 2px;
}
.history li {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
}
.history summary {
  cursor: pointer;
}
.foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 16px;
  border-top: 0.5px solid var(--separator);
}
.state {
  display: flex;
  align-items: center;
  gap: 6px;
}
.err {
  color: var(--danger);
}
.spin {
  animation: spin 1s linear infinite;
}
.pk-enter-active,
.pk-leave-active {
  transition: opacity var(--t-base) var(--ease-out);
}
.pk-enter-active .sheet {
  transition: transform var(--t-slow) var(--ease-spring);
}
.pk-leave-active .sheet {
  transition: transform var(--t-fast) var(--ease-in);
}
.pk-enter-from,
.pk-leave-to {
  opacity: 0;
}
.pk-enter-from .sheet,
.pk-leave-to .sheet {
  transform: scale(0.97);
}
@media (prefers-reduced-motion: reduce) {
  .pk-enter-active .sheet,
  .pk-leave-active .sheet,
  .item {
    transition: none;
  }
}
</style>
