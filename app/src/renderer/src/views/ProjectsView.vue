<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ArchiveRestore, FolderKanban, FolderPlus, LoaderCircle, Palette, RotateCw } from '@lucide/vue'
import type { ProjectRow } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import EmptyState from '@/components/EmptyState.vue'
import BlobImage from '@/components/BlobImage.vue'
import TweenNumber from '@/components/TweenNumber.vue'
import RelTime from '@/components/RelTime.vue'
import { useProjects } from '@/stores/projects'
import { useConnection } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'
import { normalizeStyle, styleIsEmpty } from '@/composables/projectStyle'

const projects = useProjects()
const conn = useConnection()
const toasts = useToasts()
const router = useRouter()

type Tab = 'active' | 'archived'
const tab = ref<Tab>('active')
const loading = ref(false)

async function reload(): Promise<void> {
  loading.value = true
  try {
    await projects.load({ archived: tab.value === 'archived' })
  } catch {
    /* shown through projects.error */
  } finally {
    loading.value = false
  }
}
watch([() => conn.transport, tab], ([t]) => t && void reload(), { immediate: true })

const rows = computed(() => (tab.value === 'archived' ? projects.archived : projects.list))
const ready = computed(() => (tab.value === 'archived' ? projects.archivedLoaded : projects.loaded))

const creating = ref(false)
const newName = ref('')
const newDesc = ref('')
const saving = ref(false)
const nameEl = ref<HTMLInputElement | null>(null)

async function openCreate(): Promise<void> {
  tab.value = 'active'
  creating.value = true
  await nextTick()
  nameEl.value?.focus()
}
function cancelCreate(): void {
  creating.value = false
  newName.value = ''
  newDesc.value = ''
}
async function create(): Promise<void> {
  const name = newName.value.trim()
  if (!name || saving.value) return
  saving.value = true
  try {
    const p = await projects.create({ name, description: newDesc.value.trim() })
    cancelCreate()
    toasts.push(`Đã tạo dự án ${p.name}`, 'success', 2400)
    await router.push(`/projects/${p.id}`)
  } catch (e) {
    toasts.error(e, 'Không tạo được dự án')
  } finally {
    saving.value = false
  }
}

async function restore(p: ProjectRow): Promise<void> {
  try {
    await projects.patch(p.id, { archived: false })
    toasts.push(`Đã khôi phục ${p.name}`, 'success', 2400)
  } catch (e) {
    toasts.error(e, 'Không khôi phục được')
  }
}

function styleTags(p: ProjectRow): string[] {
  const s = normalizeStyle(p.style)
  const out: string[] = []
  if (s.prompt_prefix.trim() || s.prompt_suffix.trim()) out.push('Phong cách')
  if (s.preset) out.push(`Preset ${s.preset}`)
  if (s.pixel?.size) out.push(`Pixel ${s.pixel.size}px`)
  if (s.reference_image) out.push('Ảnh tham chiếu')
  if (s.palette_image || s.pixel?.palette) out.push('Bảng màu')
  return out
}
const sum = (p: ProjectRow) => projects.summaryOf(p.id)
const drafts = (p: ProjectRow): number => sum(p)?.versions.draft ?? 0
const active = (p: ProjectRow): number => (sum(p)?.runs.running ?? 0) + (sum(p)?.runs.awaiting_human ?? 0)
</script>

<template>
  <div class="view">
    <PageHeader title="Dự án">
      <template #subtitle><TweenNumber :value="rows.length" /> dự án{{ tab === 'archived' ? ' đã lưu trữ' : '' }}</template>
      <div class="segmented" role="radiogroup" aria-label="Hiện dự án">
        <button type="button" role="radio" :aria-checked="tab === 'active'" :class="{ on: tab === 'active' }" @click="tab = 'active'">Đang dùng</button>
        <button type="button" role="radio" :aria-checked="tab === 'archived'" :class="{ on: tab === 'archived' }" @click="tab = 'archived'">Lưu trữ</button>
      </div>
      <button type="button" class="btn btn-primary" @click="openCreate"><FolderPlus /> Dự án mới</button>
    </PageHeader>

    <div class="scroll">
      <Transition name="drop">
        <form v-if="creating" class="create card" @submit.prevent="create" @keydown.esc="cancelCreate">
          <div class="c-fields">
            <input ref="nameEl" v-model="newName" class="field" maxlength="120" placeholder="Tên dự án, ví dụ: Rừng Tiên" aria-label="Tên dự án" />
            <input v-model="newDesc" class="field" maxlength="4000" placeholder="Mô tả ngắn (tuỳ chọn)" aria-label="Mô tả dự án" />
          </div>
          <div class="c-acts">
            <span class="caption">Tạo xong sẽ mở trang dự án để khoá phong cách.</span>
            <button type="button" class="btn btn-plain" @click="cancelCreate">Huỷ</button>
            <button type="submit" class="btn btn-primary" :disabled="!newName.trim() || saving">
              <LoaderCircle v-if="saving" class="spin" />
              {{ saving ? 'Đang tạo…' : 'Tạo dự án' }}
            </button>
          </div>
        </form>
      </Transition>

      <p v-if="!ready && loading" class="caption state" role="status"><LoaderCircle class="spin" :size="14" /> Đang tải dự án…</p>
      <EmptyState v-else-if="projects.error && !rows.length" :icon="FolderKanban" title="Không tải được dự án" :text="projects.error">
        <button class="btn" @click="reload"><RotateCw /> Thử lại</button>
      </EmptyState>
      <EmptyState
        v-else-if="ready && !rows.length && tab === 'archived'"
        :icon="FolderKanban"
        title="Chưa có dự án nào được lưu trữ"
        text="Dự án lưu trữ vẫn giữ lượt chạy và asset; khôi phục bất cứ lúc nào."
      >
        <button class="btn" @click="tab = 'active'">Xem dự án đang dùng</button>
      </EmptyState>
      <EmptyState
        v-else-if="ready && !rows.length && !creating"
        :icon="FolderKanban"
        title="Chưa có dự án nào"
        text="Một dự án gom asset của cùng một game và khoá phong cách: hậu tố prompt, preset, pixel art, bảng màu, ảnh tham chiếu."
      >
        <button class="btn btn-primary" @click="openCreate"><FolderPlus /> Tạo dự án đầu tiên</button>
      </EmptyState>

      <div v-else v-auto-animate class="grid">
        <article v-for="p in rows" :key="p.id" class="pcard card" :class="{ archived: p.archived, current: projects.activeId === p.id }">
          <RouterLink :to="`/projects/${p.id}`" class="cover" :aria-label="`Mở dự án ${p.name}`">
            <BlobImage v-if="sum(p)?.cover_hash" :hash="sum(p)!.cover_hash!" :size="384" />
            <span v-else class="cover-empty"><Palette :size="28" :stroke-width="1.5" /></span>
            <span v-if="projects.activeId === p.id" class="cur-tag">Đang chọn</span>
          </RouterLink>
          <div class="body">
            <RouterLink :to="`/projects/${p.id}`" class="name headline">{{ p.name }}</RouterLink>
            <p class="desc callout">{{ p.description || (styleIsEmpty(normalizeStyle(p.style)) ? 'Chưa khoá phong cách' : 'Không có mô tả') }}</p>
            <div v-if="styleTags(p).length" class="tags">
              <span v-for="t in styleTags(p)" :key="t" class="tag">{{ t }}</span>
            </div>
            <dl class="stats">
              <div>
                <dt class="caption">Lượt chạy</dt>
                <dd class="num"><TweenNumber :value="sum(p)?.runs_total ?? 0" /></dd>
              </div>
              <div>
                <dt class="caption">Đã duyệt</dt>
                <dd class="num ok"><TweenNumber :value="sum(p)?.approved_versions ?? 0" /></dd>
              </div>
              <div>
                <dt class="caption">Nháp</dt>
                <dd class="num"><TweenNumber :value="drafts(p)" /></dd>
              </div>
            </dl>
            <p class="foot caption">
              <span v-if="active(p)" class="live"><span class="dot" /> {{ active(p) }} đang chạy ·</span>
              <span>Hoạt động <RelTime :ts="Math.max(sum(p)?.last_activity ?? 0, p.updated_at)" /></span>
              <button v-if="p.archived" type="button" class="btn btn-plain small restore" @click="restore(p)"><ArchiveRestore :size="14" /> Khôi phục</button>
            </p>
          </div>
        </article>
      </div>
    </div>
  </div>
</template>

<style scoped>
.scroll {
  display: grid;
  align-content: start;
  gap: 16px;
}
.state {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.segmented {
  display: inline-flex;
  padding: 2px;
  border-radius: 8px;
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
}
.segmented button {
  height: 26px;
  padding: 0 12px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  font-weight: 500;
  cursor: pointer;
  transition:
    background-color var(--t-fast),
    box-shadow var(--t-fast);
}
.segmented button.on {
  background: var(--bg-elevated);
  box-shadow: var(--shadow-1), var(--hairline);
  font-weight: 600;
}
.create {
  display: grid;
  gap: 12px;
  padding: 14px 16px;
  max-width: 720px;
}
.c-fields {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr);
  gap: 10px;
}
.c-acts {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}
.c-acts .caption {
  margin-right: auto;
  color: var(--text-3);
}
.drop-enter-active,
.drop-leave-active {
  transition:
    opacity var(--t-base) var(--ease-out),
    transform var(--t-base) var(--ease-spring);
}
.drop-enter-from,
.drop-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 16px;
}
.pcard {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 0;
  transition:
    box-shadow var(--t-base) var(--ease-out),
    transform var(--t-fast) var(--ease-spring);
}
.pcard:hover {
  box-shadow: var(--shadow-2), var(--hairline);
}
.pcard.current {
  box-shadow: 0 0 0 1.5px var(--accent), var(--shadow-1);
}
.pcard.archived {
  opacity: 0.85;
}
.cover {
  position: relative;
  display: block;
  aspect-ratio: 16 / 9;
  overflow: hidden;
  background: radial-gradient(circle at 50% 40%, var(--accent-soft), transparent 70%), var(--bg-sunken);
}
.cover :deep(.blob) {
  width: 100%;
  height: 100%;
}
.cover-empty {
  display: grid;
  place-items: center;
  height: 100%;
  color: var(--accent);
  opacity: 0.7;
}
.cur-tag {
  position: absolute;
  top: 8px;
  left: 8px;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: var(--fs-caption);
  font-weight: 600;
  color: var(--text-on-accent);
  background: var(--accent);
}
.body {
  display: grid;
  gap: 8px;
  padding: 12px 14px 12px;
}
.name {
  color: var(--text);
  text-decoration: none;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.name:hover {
  color: var(--accent);
}
.desc {
  color: var(--text-2);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 2.6em;
}
.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.tag {
  padding: 1px 7px;
  border-radius: 999px;
  font-size: var(--fs-caption);
  color: var(--accent);
  background: var(--accent-soft);
}
.stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  margin: 2px 0 0;
  padding-top: 8px;
  border-top: 0.5px solid var(--separator);
}
.stats div {
  display: grid;
  gap: 1px;
}
.stats dd {
  margin: 0;
  font-size: var(--fs-title);
  font-weight: 650;
  letter-spacing: -0.01em;
}
.stats dd.ok {
  color: var(--st-ok);
}
.foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  color: var(--text-3);
}
.live {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--st-running);
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  animation: soft-pulse 1.2s ease-in-out infinite;
}
.restore {
  margin-left: auto;
}
.small {
  height: 24px;
  padding: 0 8px;
  font-size: var(--fs-callout);
}
.spin {
  animation: spin 1s linear infinite;
}
@media (prefers-reduced-motion: reduce) {
  .drop-enter-active,
  .drop-leave-active {
    transition: none;
  }
  .dot {
    animation: none;
  }
}
</style>
