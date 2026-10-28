<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ExternalLink, LoaderCircle, ScanSearch, Sparkles, Wand2 } from '@lucide/vue'
import type { ProjectQaAsset, ProjectQaGrade, ProjectQaReport } from '@/api/types'
import EmptyState from '@/components/EmptyState.vue'
import BlobImage from '@/components/BlobImage.vue'
import TweenNumber from '@/components/TweenNumber.vue'
import RelTime from '@/components/RelTime.vue'
import DuplicatesPanel from '@/components/DuplicatesPanel.vue'
import { api } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'

const props = defineProps<{ projectId: string }>()
const router = useRouter()
const toasts = useToasts()

const GRADE_LABEL: Record<ProjectQaGrade, string> = { ok: 'Đồng bộ', warn: 'Cần xem', bad: 'Lệch', skip: 'Bỏ qua' }
const GRADE_ORDER: ProjectQaGrade[] = ['bad', 'warn', 'ok', 'skip']

const report = ref<ProjectQaReport | null>(null)
const loading = ref(false)
const loadError = ref<string | null>(null)
const running = ref(false)
const onlyApproved = ref(false)
const vision = ref(false)
const flaggedOnly = ref(true)
const fixing = ref(new Set<string>())
const fixedRun = ref(new Map<string, string>())

async function load(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    report.value = await api().getProjectQa(props.projectId)
    onlyApproved.value = !!report.value.only_approved
  } catch (e) {
    loadError.value = (e as Error).message
  } finally {
    loading.value = false
  }
}
watch(() => props.projectId, () => void load(), { immediate: true })

async function check(): Promise<void> {
  running.value = true
  try {
    report.value = await api().runProjectQa(props.projectId, { only_approved: onlyApproved.value, vision: vision.value })
    if (report.value.vision?.error) toasts.push(`Claude chưa nhận xét được: ${report.value.vision.error.message}`, 'error', 6000)
  } catch (e) {
    toasts.error(e, 'Không kiểm tra được dự án')
  } finally {
    running.value = false
  }
}

async function fix(a: ProjectQaAsset): Promise<void> {
  fixing.value = new Set(fixing.value).add(a.version_id)
  try {
    const res = await api().fixProjectQa(props.projectId, a.version_id)
    fixedRun.value = new Map(fixedRun.value).set(a.version_id, res.run_id)
    toasts.push(`Đã bắt đầu sửa “${a.asset_name}”`, 'success')
  } catch (e) {
    toasts.error(e, 'Không bắt đầu sửa được')
  } finally {
    const next = new Set(fixing.value)
    next.delete(a.version_id)
    fixing.value = next
  }
}

const hasReport = computed(() => !!report.value?.created_at)
const shown = computed(() => (report.value?.assets ?? []).filter((a) => !flaggedOnly.value || a.issues.length || a.vision_note))
const counts = computed(() => report.value?.summary?.counts ?? { ok: 0, warn: 0, bad: 0, skip: 0 })
const ref_ = computed(() => report.value?.reference)
</script>

<template>
  <section class="pqa">
    <div class="bar">
      <div v-if="hasReport" class="chips" aria-label="Kết quả kiểm tra">
        <span v-for="g in GRADE_ORDER" :key="g" class="chip" :class="`g-${g}`" :data-zero="!counts[g]">{{ GRADE_LABEL[g] }} <b class="num">{{ counts[g] ?? 0 }}</b></span>
      </div>
      <span v-if="hasReport" class="caption">Kiểm lúc <RelTime :ts="report!.created_at!" /></span>
      <div class="actions">
        <label class="opt caption"><input v-model="onlyApproved" type="checkbox" /> Chỉ asset đã duyệt</label>
        <label class="opt caption" title="Claude xem cả bộ trên một ảnh ghép và nhận xét (tốn phí API)"><input v-model="vision" type="checkbox" /> <Sparkles :size="12" /> Claude nhận xét</label>
        <button type="button" class="btn btn-primary" :disabled="running" @click="check">
          <LoaderCircle v-if="running" class="spin" /> <ScanSearch v-else />
          {{ running ? (vision ? 'Đang đo và chờ Claude…' : 'Đang đo…') : hasReport ? 'Kiểm tra lại' : 'Kiểm tra đồng bộ' }}
        </button>
      </div>
    </div>

    <p v-if="loading && !report" class="caption state" role="status"><LoaderCircle class="spin" :size="14" /> Đang tải báo cáo…</p>
    <EmptyState v-else-if="loadError" :icon="ScanSearch" title="Không tải được báo cáo" :text="loadError">
      <button class="btn" @click="load">Thử lại</button>
    </EmptyState>
    <EmptyState
      v-else-if="!hasReport"
      :icon="ScanSearch"
      title="Chưa kiểm tra đồng bộ"
      text="So mọi ảnh của dự án với nhau: lệch bảng màu, tỉ lệ chủ thể, độ dày nét viền, hướng ánh sáng. Asset lệch có nút sửa 1 chạm."
    >
      <button class="btn btn-primary" :disabled="running" @click="check"><ScanSearch /> Kiểm tra đồng bộ</button>
    </EmptyState>

    <template v-else>
      <div class="overview card">
        <div class="score">
          <span class="big num"><TweenNumber v-if="report!.summary?.score != null" :value="report!.summary!.score!" /><template v-else>—</template></span>
          <span class="caption">điểm đồng bộ · {{ report!.summary?.scored ?? 0 }}/{{ report!.summary?.total ?? 0 }} ảnh</span>
        </div>
        <div v-if="ref_" class="refs">
          <div v-if="ref_.palette.length" class="ref">
            <span class="caption">{{ ref_.palette_source }}</span>
            <span class="swatches"><span v-for="c in ref_.palette" :key="c" class="sw" :style="{ background: c }" :title="c" /></span>
          </div>
          <div class="ref caption">
            <span v-if="ref_.light_label">Ánh sáng chung: <b>{{ ref_.light_label }}</b></span>
            <span v-if="ref_.outlined !== null">Viền: <b>{{ ref_.outlined ? `có${ref_.outline_median ? ` (~${ref_.outline_median} px/256)` : ''}` : 'không' }}</b></span>
            <span v-if="ref_.fill_median">Chủ thể cao ~<b>{{ Math.round(ref_.fill_median * 100) }}%</b> khung</span>
          </div>
        </div>
        <p v-if="report!.vision?.summary" class="vision"><Sparkles :size="13" /> {{ report!.vision.summary }}</p>
      </div>

      <div class="list card">
        <div class="head">
          <label class="opt caption"><input v-model="flaggedOnly" type="checkbox" /> Chỉ asset có vấn đề</label>
          <span class="caption">{{ shown.length }} asset</span>
        </div>
        <p v-if="!shown.length" class="caption none">Không asset nào lệch đáng kể.</p>
        <div v-auto-animate>
          <div v-for="a in shown" :key="a.version_id" class="row">
            <BlobImage :hash="a.blob.hash" :size="128" :alt="a.asset_name" class="thumb" />
            <div class="info">
              <div class="title">
                <span class="name">{{ a.asset_name }}</span>
                <span class="grade" :class="`g-${a.grade}`"><b v-if="a.score !== null" class="num">{{ a.score }}</b> {{ GRADE_LABEL[a.grade] }}</span>
                <span class="swatches small"><span v-for="c in a.colors" :key="c" class="sw" :style="{ background: c }" :title="c" /></span>
              </div>
              <ul v-if="a.issues.length" class="issues">
                <li v-for="i in a.issues" :key="i.key" :class="`s-${i.severity}`"><b>{{ i.label }}:</b> {{ i.message }}</li>
              </ul>
              <p v-if="a.vision_note" class="caption vnote"><Sparkles :size="12" /> {{ a.vision_note }}</p>
              <p v-if="a.note && !a.issues.length" class="caption">{{ a.note }}</p>
            </div>
            <div class="acts">
              <button v-if="fixedRun.get(a.version_id)" type="button" class="btn btn-plain" @click="router.push(`/runs/${fixedRun.get(a.version_id)}`)"><ExternalLink /> Xem bản sửa</button>
              <button v-else-if="a.fix" type="button" class="btn" :disabled="fixing.has(a.version_id)" :title="String(a.fix.inputs.instructions ?? '')" @click="fix(a)">
                <LoaderCircle v-if="fixing.has(a.version_id)" class="spin" /> <Wand2 v-else /> Sửa 1 chạm
              </button>
              <RouterLink :to="`/runs/${a.run_id}`" class="btn btn-plain btn-icon" title="Mở lượt chạy gốc" :aria-label="`Mở lượt chạy của ${a.asset_name}`"><ExternalLink /></RouterLink>
            </div>
          </div>
        </div>
      </div>
    </template>
    <DuplicatesPanel :project-id="projectId" />
  </section>
</template>

<style scoped>
.pqa {
  display: grid;
  gap: 12px;
}
.bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.chip,
.grade {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 22px;
  padding: 0 9px;
  border-radius: 999px;
  font-size: var(--fs-callout);
  color: var(--text-2);
  background: var(--bg-active);
  white-space: nowrap;
  transition: opacity var(--t-base);
}
.chip[data-zero='true'] {
  opacity: 0.45;
}
.g-ok {
  color: var(--st-ok);
  background: var(--st-ok-soft);
}
.g-warn {
  color: var(--st-waiting);
  background: var(--st-human-soft);
}
.g-bad {
  color: var(--st-fail);
  background: var(--st-fail-soft);
}
.actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-left: auto;
}
.opt {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  cursor: pointer;
}
.state {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.overview {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 10px 24px;
  align-items: center;
  padding: 14px 18px;
}
.score {
  display: grid;
  justify-items: start;
}
.big {
  font-size: 30px;
  font-weight: 700;
  line-height: 1.1;
}
.refs {
  display: grid;
  gap: 6px;
}
.ref {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 14px;
}
.vision {
  grid-column: 1 / -1;
  margin: 0;
  display: flex;
  gap: 6px;
  align-items: baseline;
  color: var(--text-2);
}
.swatches {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 3px;
}
.sw {
  width: 14px;
  height: 14px;
  border-radius: 4px;
  box-shadow: inset 0 0 0 0.5px rgb(0 0 0 / 0.25);
}
.swatches.small .sw {
  width: 10px;
  height: 10px;
  border-radius: 3px;
}
.list {
  padding: 4px 0 8px;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 14px;
}
.none {
  padding: 8px 14px;
  margin: 0;
}
.row {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr) auto;
  gap: 12px;
  align-items: start;
  padding: 10px 14px;
  border-top: 0.5px solid var(--separator);
}
.thumb {
  width: 64px;
  height: 64px;
  border-radius: 8px;
  overflow: hidden;
}
.info {
  display: grid;
  gap: 4px;
  min-width: 0;
}
.title {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.issues {
  margin: 0;
  padding-left: 16px;
  display: grid;
  gap: 2px;
  font-size: var(--fs-callout);
}
.s-bad::marker {
  color: var(--st-fail);
}
.s-warn::marker {
  color: var(--st-waiting);
}
.vnote {
  margin: 0;
  display: flex;
  gap: 5px;
  align-items: baseline;
}
.acts {
  display: flex;
  align-items: center;
  gap: 6px;
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
  .overview {
    grid-template-columns: 1fr;
  }
  .row {
    grid-template-columns: 48px minmax(0, 1fr);
  }
  .thumb {
    width: 48px;
    height: 48px;
  }
  .acts {
    grid-column: 2;
  }
}
</style>
