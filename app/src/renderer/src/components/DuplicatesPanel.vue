<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Copy, LoaderCircle, ScanSearch, X } from '@lucide/vue'
import type { DuplicateGroup, DuplicatesReport } from '@/api/types'
import BlobImage from './BlobImage.vue'
import { api } from '@/stores/connection'
import { useVersions } from '@/stores/versions'
import { useToasts } from '@/stores/toasts'
import { DUP_LABEL } from '@/composables/similar'

const props = defineProps<{ projectId: string }>()
const router = useRouter()
const versions = useVersions()
const toasts = useToasts()

const THRESHOLDS = [
  { value: 0.92, label: 'Rộng' },
  { value: 0.95, label: 'Vừa' },
  { value: 0.98, label: 'Chặt' }
]
const report = ref<DuplicatesReport | null>(null)
const running = ref(false)
const threshold = ref(0.95)
const rejecting = ref(new Set<string>())
const rejected = ref(new Set<string>())

async function check(): Promise<void> {
  running.value = true
  try {
    report.value = await api().projectDuplicates(props.projectId, { threshold: threshold.value })
  } catch (e) {
    toasts.error(e, 'Không dò được asset trùng')
  } finally {
    running.value = false
  }
}
watch(
  () => props.projectId,
  () => (report.value = null)
)
watch(threshold, () => report.value && void check())

const groups = computed(() => report.value?.groups ?? [])
const best = (g: DuplicateGroup, id: string): number | null => {
  const s = g.pairs.filter((p) => p.a === id || p.b === id).map((p) => p.score ?? 0)
  return s.length ? Math.max(...s) : null
}

async function reject(versionId: string): Promise<void> {
  rejecting.value = new Set(rejecting.value).add(versionId)
  try {
    if (versions.map.has(versionId)) await versions.setStatus(versionId, 'reject')
    else await api().setVersionStatus(versionId, 'reject')
    rejected.value = new Set(rejected.value).add(versionId)
  } catch (e) {
    toasts.error(e, 'Không bỏ được bản này')
  } finally {
    const next = new Set(rejecting.value)
    next.delete(versionId)
    rejecting.value = next
  }
}
</script>

<template>
  <section class="dups card" aria-labelledby="dup-title">
    <div class="head">
      <div class="who">
        <h3 id="dup-title" class="title"><Copy :size="14" aria-hidden="true" /> Asset trùng lặp</h3>
        <span class="caption">Ảnh giống hệt hoặc gần như nhau trong dự án: giữ một bản, bỏ các bản thừa.</span>
      </div>
      <div class="seg" role="radiogroup" aria-label="Độ chặt">
        <button
          v-for="t in THRESHOLDS"
          :key="t.value"
          type="button"
          role="radio"
          :aria-checked="threshold === t.value"
          :class="{ on: threshold === t.value }"
          :title="`Giống từ ${Math.round(t.value * 100)}% trở lên`"
          @click="threshold = t.value"
        >
          {{ t.label }}
        </button>
      </div>
      <button type="button" class="btn" :disabled="running" @click="check">
        <LoaderCircle v-if="running" class="spin" /> <ScanSearch v-else /> {{ running ? 'Đang dò…' : report ? 'Dò lại' : 'Dò trùng lặp' }}
      </button>
    </div>

    <template v-if="report">
      <p class="caption meta">
        Đã so {{ report.checked }} ảnh · {{ groups.length ? `${groups.length} nhóm trùng` : 'không có nhóm trùng nào' }}
        <template v-if="report.mode === 'phash'"> · chưa có model CLIP, chỉ bắt ảnh gần như trùng</template>
        <template v-if="report.pending"> · còn {{ report.pending }} ảnh đang lập chỉ mục</template>
      </p>
      <div v-auto-animate class="groups">
        <div v-for="(g, gi) in groups" :key="g.members.map((m) => m.version_id).join('|')" class="group">
          <span class="kind" :class="`k-${g.kind}`">{{ DUP_LABEL[g.kind] }}</span>
          <ul class="members" :aria-label="`Nhóm trùng ${gi + 1}`">
            <li v-for="m in g.members" :key="m.version_id" class="m" :class="{ gone: rejected.has(m.version_id) }">
              <button type="button" class="thumb" :title="`${m.asset_name} · mở lượt chạy`" @click="router.push(`/runs/${m.run_id}`)">
                <BlobImage :hash="m.blob.hash" :size="128" :alt="m.asset_name" />
              </button>
              <span class="nm" :title="m.asset_name">{{ m.asset_name }}</span>
              <span class="caption num">
                <template v-if="m.status === 'approved'">Đã duyệt</template>
                <template v-else-if="best(g, m.version_id) != null">{{ Math.round(best(g, m.version_id)! * 100) }}%</template>
              </span>
              <button
                v-if="!rejected.has(m.version_id)"
                type="button"
                class="btn btn-plain btn-icon rej"
                :disabled="rejecting.has(m.version_id)"
                :aria-label="`Bỏ bản ${m.asset_name}`"
                title="Bỏ bản này (từ chối)"
                @click="reject(m.version_id)"
              >
                <LoaderCircle v-if="rejecting.has(m.version_id)" class="spin" /> <X v-else />
              </button>
            </li>
          </ul>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.dups {
  display: grid;
  gap: 8px;
  padding: 12px 14px;
}
.head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
}
.who {
  flex: 1;
  min-width: 220px;
  display: grid;
}
.title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: var(--fs-body);
  font-weight: 600;
}
.seg {
  display: inline-flex;
  padding: 2px;
  border-radius: var(--r-sm);
  background: var(--bg-sunken);
}
.seg button {
  height: 24px;
  padding: 0 10px;
  border: 0;
  border-radius: calc(var(--r-sm) - 2px);
  background: transparent;
  color: var(--text-2);
  font: inherit;
  font-size: var(--fs-callout);
  cursor: pointer;
  transition:
    background-color var(--t-fast),
    color var(--t-fast);
}
.seg button.on {
  background: var(--bg-elevated);
  color: var(--text);
  box-shadow: var(--hairline);
}
.meta {
  margin: 0;
}
.groups {
  display: grid;
  gap: 8px;
}
.group {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr);
  gap: 10px;
  align-items: start;
  padding-top: 8px;
  border-top: 0.5px solid var(--separator);
}
.kind {
  justify-self: start;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: var(--fs-caption);
  font-weight: 600;
  color: var(--text-2);
  background: var(--bg-active);
}
.k-exact {
  color: var(--st-fail);
  background: var(--st-fail-soft);
}
.k-near {
  color: var(--st-waiting);
  background: var(--st-human-soft);
}
.members {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.m {
  position: relative;
  width: 96px;
  display: grid;
  gap: 2px;
  transition: opacity var(--t-base);
}
.m.gone {
  opacity: 0.35;
}
.thumb {
  width: 96px;
  height: 96px;
  padding: 0;
  border: 0;
  border-radius: 8px;
  overflow: hidden;
  background: var(--bg-sunken);
  cursor: pointer;
  box-shadow: var(--hairline);
}
.thumb:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.nm {
  font-size: var(--fs-callout);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.num {
  font-variant-numeric: tabular-nums;
}
.rej {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 24px;
  height: 24px;
  background: var(--bg-elevated);
  opacity: 0;
  transition: opacity var(--t-fast);
}
.m:hover .rej,
.rej:focus-visible {
  opacity: 1;
}
.spin {
  animation: spin 1s linear infinite;
}
@media (prefers-reduced-motion: reduce) {
  .spin {
    animation: none;
  }
}
@media (max-width: 720px) {
  .group {
    grid-template-columns: 1fr;
  }
}
</style>
