<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Lock, LoaderCircle, Palette, RefreshCw, ScanSearch } from '@lucide/vue'
import type { StyleBible, StyleDriftReport } from '@/api/types'
import BlobImage from '@/components/BlobImage.vue'
import BlobThumb from '@/components/BlobThumb.vue'
import RelTime from '@/components/RelTime.vue'
import { api } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'

const props = defineProps<{ projectId: string }>()
const toasts = useToasts()

const bible = ref<StyleBible | null>(null)
const report = ref<StyleDriftReport | null>(null)
const checking = ref(false)
const restyling = ref(false)
const picked = ref<Set<string>>(new Set())

const lock = computed(() => bible.value?.lock ?? null)
const drifted = computed(() => report.value?.assets.filter((a) => a.drifted) ?? [])
const allPicked = computed(() => drifted.value.length > 0 && drifted.value.every((a) => picked.value.has(a.version_id)))

async function load(): Promise<void> {
  report.value = null
  picked.value = new Set()
  try {
    bible.value = (await api().getStyleBible(props.projectId)).bible
  } catch {
    bible.value = null
  }
}
watch(() => props.projectId, () => void load(), { immediate: true })

async function check(): Promise<void> {
  checking.value = true
  try {
    report.value = await api().getStyleDrift(props.projectId)
    picked.value = new Set()
  } catch (e) {
    toasts.error(e, 'Không kiểm được độ lệch style')
  } finally {
    checking.value = false
  }
}

function toggle(id: string): void {
  const next = new Set(picked.value)
  if (!next.delete(id)) next.add(id)
  picked.value = next
}
const toggleAll = (): void => {
  picked.value = allPicked.value ? new Set() : new Set(drifted.value.map((a) => a.version_id))
}

async function restyle(ids: string[]): Promise<void> {
  if (!ids.length || restyling.value) return
  restyling.value = true
  try {
    const res = await api().restyleProjectAssets(props.projectId, { version_ids: ids })
    const n = res.started.length
    const skip = res.skipped.length
    toasts.push(
      n ? `Đã chạy lại ${n} asset với style mới${skip ? `, bỏ qua ${skip}` : ''}` : `Không chạy lại được: ${res.skipped[0]?.reason ?? 'không rõ'}`,
      n ? 'success' : 'error',
      n ? 3000 : 6000
    )
    picked.value = new Set()
  } catch (e) {
    toasts.error(e, 'Không chạy lại được')
  } finally {
    restyling.value = false
  }
}
</script>

<template>
  <section class="slp card">
    <div class="head">
      <span class="f-label"><Lock :size="14" aria-hidden="true" /> Style đang khoá</span>
      <button v-if="lock" type="button" class="btn" :disabled="checking" title="Chấm các asset gần đây so với bảng màu đã khoá" @click="check">
        <LoaderCircle v-if="checking" class="spin" /> <ScanSearch v-else /> {{ checking ? 'Đang kiểm…' : report ? 'Kiểm lại' : 'Kiểm độ lệch' }}
      </button>
    </div>

    <p v-if="!lock" class="caption note">
      Chưa khoá style. Chạy công thức “Khoá style toàn Dự án”, duyệt xong rồi bấm “Đặt làm style project” ở trang lượt chạy.
    </p>
    <template v-else>
      <div class="ref">
        <figure v-for="(r, i) in bible?.refs ?? []" :key="r.hash" class="refimg">
          <BlobImage :hash="r.hash" :size="128" :alt="`Ảnh style ${i + 1}`" class="thumb" />
        </figure>
        <div class="meta">
          <span class="swatches"><span v-for="c in lock.palette" :key="c" class="sw" :style="{ background: c }" :title="c" /><Palette v-if="!lock.palette.length" :size="14" /></span>
          <span class="caption">
            Từ lượt chạy
            <RouterLink :to="`/runs/${lock.run_id}`" class="mono" :title="lock.run_id">{{ lock.run_id.slice(0, 8) }}</RouterLink>
            · <RelTime :ts="lock.locked_at" />
          </span>
        </div>
      </div>

      <p v-if="report && !drifted.length" class="caption note" role="status">Không có asset nào lệch style trong {{ report.summary.total }} asset gần đây.</p>
      <template v-if="drifted.length">
        <div class="bar">
          <label class="opt caption"><input type="checkbox" :checked="allPicked" @change="toggleAll" /> Chọn hết ({{ drifted.length }} asset lệch)</label>
          <button type="button" class="btn btn-primary" :disabled="!picked.size || restyling" @click="restyle([...picked])">
            <LoaderCircle v-if="restyling" class="spin" /> <RefreshCw v-else /> Chạy lại {{ picked.size || '' }} đã chọn
          </button>
        </div>
        <ul class="list">
          <li v-for="a in drifted" :key="a.version_id" class="row">
            <input type="checkbox" :checked="picked.has(a.version_id)" :aria-label="`Chọn ${a.asset_name}`" @change="toggle(a.version_id)" />
            <BlobThumb :blob="a.blob" :size="128" class="rthumb" />
            <div class="info">
              <span class="name" :title="a.asset_name">{{ a.asset_name }}</span>
              <span class="caption clip" :title="a.issues.map((i) => i.message).join('\n')">{{ a.issues[0]?.message ?? a.note }}</span>
            </div>
            <span class="score num" :data-grade="a.grade" title="Điểm giống style (cao hơn là giống hơn)">{{ a.score === null ? '—' : Math.round(a.score) }}</span>
            <button type="button" class="btn btn-plain" :disabled="restyling" @click="restyle([a.version_id])">
              <RefreshCw /> Chạy lại với style này
            </button>
          </li>
        </ul>
      </template>
    </template>
  </section>
</template>

<style scoped>
.slp {
  display: grid;
  gap: 10px;
  padding: 12px 14px;
}
.head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px 12px;
}
.f-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.note {
  margin: 0;
}
.ref {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 16px;
}
.refimg {
  margin: 0;
}
.thumb {
  width: 64px;
  height: 64px;
  border-radius: 8px;
}
.meta {
  display: grid;
  gap: 6px;
}
.swatches {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 4px;
}
.sw {
  width: 22px;
  height: 22px;
  border-radius: 6px;
  box-shadow: inset 0 0 0 0.5px rgb(0 0 0 / 0.25);
}
.bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px 12px;
}
.opt {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}
.list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 6px;
}
.row {
  display: grid;
  grid-template-columns: auto 48px minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 10px;
}
.rthumb {
  width: 48px;
  height: 48px;
  border-radius: 6px;
}
.info {
  display: grid;
  min-width: 0;
}
.name,
.clip {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.score {
  font-weight: 600;
}
.score[data-grade='bad'] {
  color: var(--danger);
}
.score[data-grade='warn'] {
  color: var(--st-waiting);
}
</style>
