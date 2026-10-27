<script setup lang="ts">
import { computed, ref, shallowRef, watch } from 'vue'
import { useElementSize, watchDebounced } from '@vueuse/core'
import { ArrowDown, ArrowUp, LoaderCircle, RotateCw } from '@lucide/vue'
import type { DailyStats, RecipeStats, StatsOverview } from '@/api/types'
import TweenNumber from './TweenNumber.vue'
import ProjectSelect from './ProjectSelect.vue'
import RelTime from './RelTime.vue'
import { api, useConnection } from '@/stores/connection'
import { useRecipes } from '@/stores/recipes'
import { useRuns } from '@/stores/runs'
import { humanDuration, kindName, stepLabel } from '@/composables/format'

const conn = useConnection()
const recipes = useRecipes()
const runs = useRuns()

type Period = '7' | '30' | '90' | 'all'
const PERIODS: Array<{ id: Period; label: string }> = [
  { id: '7', label: '7 ngày' },
  { id: '30', label: '30 ngày' },
  { id: '90', label: '90 ngày' },
  { id: 'all', label: 'Tất cả' }
]
const KEY = 'studio.dash.stats'

function readPrefs(): { period: Period; project: string } {
  try {
    const v = JSON.parse(sessionStorage.getItem(KEY) ?? '{}') as { period?: Period; project?: string }
    return { period: PERIODS.some((p) => p.id === v.period) ? v.period! : '30', project: v.project ?? 'all' }
  } catch {
    return { period: '30', project: 'all' }
  }
}
const prefs = readPrefs()
const period = ref<Period>(prefs.period)
const project = ref(prefs.project)
watch([period, project], ([p, pr]) => {
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ period: p, project: pr }))
  } catch {
    /* storage unavailable */
  }
})

const data = shallowRef<StatsOverview | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
let seq = 0

// Same local midnight the core's daily buckets start at, so the cards add up to the bars.
function chartStart(days: number): number {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  d.setDate(d.getDate() - (days - 1))
  return d.getTime() / 1000
}

async function load(): Promise<void> {
  if (!conn.transport) return
  const my = ++seq
  loading.value = true
  const days = period.value === 'all' ? undefined : Number(period.value)
  try {
    const res = await api().statsOverview({
      since: days ? chartStart(days) : undefined,
      days,
      project_id: project.value === 'all' ? undefined : project.value,
      tz_offset_s: -new Date().getTimezoneOffset() * 60
    })
    if (my !== seq) return
    data.value = res
    error.value = null
  } catch (e) {
    if (my === seq) error.value = (e as Error).message
  } finally {
    if (my === seq) loading.value = false
  }
}
watch([period, project, () => conn.transport], () => void load(), { immediate: true })
// A run starting or ending changes every number here; batch bursts (several runs together) into one reload.
const runPulse = computed(() => {
  let done = 0
  for (const r of runs.runList) if (r.status !== 'running' && r.status !== 'awaiting_human') done += 1
  return `${runs.runList.length}:${done}:${runs.runningRuns.length}`
})
watchDebounced(runPulse, () => void load(), { debounce: 1500 })

const totals = computed(() => data.value?.totals)
const empty = computed(() => !!data.value && data.value.totals.runs === 0)

function usd(n: number): string {
  if (n > 0 && n < 0.005) return '<$0.01'
  return `$${n.toFixed(2)}`
}
function dur(n: number | null | undefined): string {
  return n == null ? '—' : humanDuration(n)
}
function pct(r: number | null | undefined): string {
  return r == null ? '—' : `${Math.round(r * 100)}%`
}
function stepTitle(kind: string): string {
  const long = kindName(kind)
  return long !== kind ? long : stepLabel(kind)
}
function recipeName(id: string): string {
  return recipes.byId(id)?.name ?? id
}

type SortKey = 'name' | 'runs' | 'cost' | 'avg' | 'p90' | 'fail'
const sortKey = ref<SortKey>('cost')
const sortDesc = ref(true)
const COLS: Array<{ key: SortKey; label: string; num: boolean }> = [
  { key: 'name', label: 'Recipe', num: false },
  { key: 'runs', label: 'Lượt', num: true },
  { key: 'cost', label: 'Chi phí', num: true },
  { key: 'avg', label: 'TB', num: true },
  { key: 'p90', label: 'p90', num: true },
  { key: 'fail', label: 'Lỗi', num: true }
]
function sortValue(r: RecipeStats, k: SortKey): number | string {
  switch (k) {
    case 'name':
      return recipeName(r.recipe_id)
    case 'runs':
      return r.runs
    case 'cost':
      return r.cost_usd
    case 'avg':
      return r.duration_s.avg ?? -1
    case 'p90':
      return r.duration_s.p90 ?? -1
    case 'fail':
      return r.failure_rate ?? -1
  }
}
function setSort(k: SortKey): void {
  if (sortKey.value === k) sortDesc.value = !sortDesc.value
  else {
    sortKey.value = k
    sortDesc.value = k !== 'name'
  }
}
const rows = computed(() => {
  const list = [...(data.value?.recipes ?? [])]
  const k = sortKey.value
  const dir = sortDesc.value ? -1 : 1
  return list.sort((a, b) => {
    const x = sortValue(a, k)
    const y = sortValue(b, k)
    if (x === y) return b.runs - a.runs
    if (typeof x === 'string' && typeof y === 'string') return x.localeCompare(y, 'vi', { sensitivity: 'base' }) * dir
    return (x < y ? -1 : 1) * dir
  })
})

const steps = computed(() => data.value?.steps ?? [])
const stepsByCost = computed(() => steps.value.some((s) => s.cost_usd > 0))
const stepMax = computed(() => Math.max(1e-9, ...steps.value.map((s) => (stepsByCost.value ? s.cost_usd : s.total_s))))

type Metric = 'cost' | 'runs' | 'time'
const metric = ref<Metric>('cost')
const METRICS: Array<{ id: Metric; label: string }> = [
  { id: 'cost', label: 'Chi phí' },
  { id: 'runs', label: 'Lượt' },
  { id: 'time', label: 'Giờ máy' }
]
function metricOf(d: DailyStats): number {
  return metric.value === 'cost' ? d.cost_usd : metric.value === 'runs' ? d.runs : d.work_s
}
function metricText(n: number): string {
  return metric.value === 'cost' ? usd(n) : metric.value === 'runs' ? `${Math.round(n)} lượt` : humanDuration(n)
}

const chartEl = ref<HTMLElement | null>(null)
const { width: chartW } = useElementSize(chartEl)
const CH = 120
const days = computed(() => data.value?.daily.days ?? [])
const maxVal = computed(() => Math.max(0, ...days.value.map(metricOf)))
const bars = computed(() => {
  const n = days.value.length
  const w = Math.max(1, chartW.value)
  const slot = n ? w / n : w
  const gap = Math.min(4, slot * 0.25)
  const bw = Math.max(1, Math.min(28, slot - gap))
  const r = Math.min(4, bw / 2)
  return days.value.map((d, i) => {
    const v = metricOf(d)
    const h = maxVal.value > 0 && v > 0 ? Math.max(2, (v / maxVal.value) * (CH - 4)) : 0
    const x = i * slot + (slot - bw) / 2
    const rr = Math.min(r, h)
    const y = CH - h
    // Rounded data end, square at the baseline.
    const path = h
      ? `M${x},${CH} L${x},${y + rr} Q${x},${y} ${x + rr},${y} L${x + bw - rr},${y} Q${x + bw},${y} ${x + bw},${y + rr} L${x + bw},${CH} Z`
      : `M${x},${CH} L${x},${CH} L${x + bw},${CH} L${x + bw},${CH} Z`
    return { d, i, x, slot, path }
  })
})
const hover = ref<number | null>(null)
const hoverBar = computed(() => (hover.value == null ? null : bars.value[hover.value]))
function dayLabel(day: string): string {
  const [, m, d] = day.split('-')
  return `${Number(d)}/${Number(m)}`
}
function onMove(e: PointerEvent): void {
  const n = bars.value.length
  if (!n || !chartEl.value) return
  const x = e.clientX - chartEl.value.getBoundingClientRect().left
  hover.value = Math.max(0, Math.min(n - 1, Math.floor((x / Math.max(1, chartW.value)) * n)))
}
const tipLeft = computed(() => {
  const b = hoverBar.value
  if (!b) return 0
  const w = chartW.value
  return w < 140 ? w / 2 : Math.max(70, Math.min(w - 70, b.x + b.slot / 2))
})
</script>

<template>
  <section class="sec" aria-labelledby="sec-cost">
    <div class="sec-head">
      <h2 id="sec-cost" class="section-label">
        Chi phí &amp; thời gian
        <LoaderCircle v-if="loading && data" :size="13" class="spin busy" aria-label="Đang cập nhật" />
      </h2>
      <div class="controls">
        <div class="seg" role="radiogroup" aria-label="Khoảng thời gian">
          <button v-for="p in PERIODS" :key="p.id" type="button" role="radio" :aria-checked="period === p.id" :class="{ on: period === p.id }" @click="period = p.id">
            {{ p.label }}
          </button>
        </div>
        <ProjectSelect v-model="project" all-label="Mọi dự án" label="Lọc chi phí theo dự án" />
      </div>
    </div>

    <div v-if="!data && loading" class="card skel-wrap" role="status" aria-label="Đang tải thống kê">
      <span v-for="i in 4" :key="i" class="skeleton" />
    </div>
    <div v-else-if="!data && error" class="card state">
      <p class="callout">Không tải được thống kê: {{ error }}</p>
      <button type="button" class="btn" @click="load"><RotateCw /> Thử lại</button>
    </div>
    <template v-else-if="data && totals">
      <p v-if="error" class="caption stale" role="alert">
        Không cập nhật được ({{ error }}), đang hiện số liệu cũ.
        <button type="button" class="btn btn-plain mini" @click="load"><RotateCw :size="12" /> Thử lại</button>
      </p>
      <div class="kpis" :class="{ dim: loading }">
        <div class="card kpi">
          <span class="caption">Tổng chi phí</span>
          <TweenNumber class="kpi-v" :value="totals.cost_usd" :format="usd" />
          <span class="caption">TB {{ usd(totals.avg_cost_usd) }} / lượt</span>
        </div>
        <div class="card kpi">
          <span class="caption">Số lượt</span>
          <TweenNumber class="kpi-v" :value="totals.runs" />
          <span class="caption">
            <TweenNumber :value="totals.succeeded" /> hoàn tất<template v-if="totals.active"> · <TweenNumber :value="totals.active" /> đang chạy</template>
          </span>
        </div>
        <div class="card kpi">
          <span class="caption">Thời gian TB</span>
          <TweenNumber v-if="totals.duration_s.avg != null" class="kpi-v" :value="totals.duration_s.avg" :format="humanDuration" />
          <span v-else class="kpi-v num">—</span>
          <span class="caption" title="Lượt hoàn tất, không tính lúc chờ bạn duyệt">p90 {{ dur(totals.duration_s.p90) }}</span>
        </div>
        <div class="card kpi">
          <span class="caption">Tỉ lệ lỗi</span>
          <TweenNumber v-if="totals.failure_rate != null" class="kpi-v" :class="{ bad: totals.failure_rate > 0.2 }" :value="totals.failure_rate * 100" :format="(n) => `${Math.round(n)}%`" />
          <span v-else class="kpi-v num">—</span>
          <span class="caption"><TweenNumber :value="totals.failed + totals.interrupted" /> lỗi · <TweenNumber :value="totals.cancelled" /> huỷ</span>
        </div>
      </div>

      <p v-if="empty" class="idle callout">Chưa có lượt chạy nào trong khoảng này.</p>
      <template v-else>
        <div class="card chart-card" :class="{ dim: loading }">
          <div class="chart-head">
            <span class="headline">Theo ngày</span>
            <div class="seg" role="radiogroup" aria-label="Chỉ số biểu đồ">
              <button v-for="m in METRICS" :key="m.id" type="button" role="radio" :aria-checked="metric === m.id" :class="{ on: metric === m.id }" @click="metric = m.id">
                {{ m.label }}
              </button>
            </div>
          </div>
          <div class="chart-body">
            <span class="y-max caption num">{{ maxVal > 0 ? metricText(maxVal) : '' }}</span>
            <div ref="chartEl" class="chart" @pointermove="onMove" @pointerleave="hover = null">
              <svg :width="chartW" :height="CH" :viewBox="`0 0 ${Math.max(1, chartW)} ${CH}`" role="img" :aria-label="`Biểu đồ ${METRICS.find((m) => m.id === metric)!.label.toLowerCase()} theo ngày`">
                <line x1="0" :x2="chartW" :y1="CH - 0.5" :y2="CH - 0.5" class="base" />
                <line x1="0" :x2="chartW" y1="4.5" y2="4.5" class="grid" />
                <path v-for="b in bars" :key="b.d.day" class="bar" :class="{ on: hover === b.i, off: hover != null && hover !== b.i }" :style="{ d: `path('${b.path}')` }" />
              </svg>
              <span v-if="maxVal === 0" class="chart-empty caption">{{ metric === 'cost' ? 'Không có chi phí Claude/API trong khoảng này' : 'Chưa có dữ liệu' }}</span>
              <div v-if="hoverBar" class="tip" :style="{ left: `${tipLeft}px` }" role="tooltip">
                <span class="caption">{{ dayLabel(hoverBar.d.day) }}</span>
                <span class="tip-v num">{{ metricText(metricOf(hoverBar.d)) }}</span>
                <span class="caption num">{{ hoverBar.d.runs }} lượt · {{ usd(hoverBar.d.cost_usd) }}<template v-if="hoverBar.d.failed"> · {{ hoverBar.d.failed }} lỗi</template></span>
              </div>
            </div>
            <div class="x-axis caption num">
              <span>{{ days[0] ? dayLabel(days[0].day) : '' }}</span>
              <span>{{ days.at(-1) ? dayLabel(days.at(-1)!.day) : '' }}</span>
            </div>
          </div>
        </div>

        <div class="grid2">
          <div class="card table-card" :class="{ dim: loading }">
            <table class="tbl">
              <thead>
                <tr>
                  <th v-for="c in COLS" :key="c.key" :class="{ n: c.num }" :aria-sort="sortKey === c.key ? (sortDesc ? 'descending' : 'ascending') : 'none'">
                    <button type="button" class="th-btn" :class="{ on: sortKey === c.key }" @click="setSort(c.key)">
                      {{ c.label }}
                      <component :is="sortDesc ? ArrowDown : ArrowUp" v-if="sortKey === c.key" :size="11" />
                    </button>
                  </th>
                </tr>
              </thead>
              <tbody v-auto-animate>
                <tr v-for="r in rows" :key="r.recipe_id">
                  <td>
                    <div class="name">
                      <span class="r-name" :title="r.recipe_id">{{ recipeName(r.recipe_id) }}</span>
                      <span v-if="r.last_run_at" class="caption">
                        <RelTime :ts="r.last_run_at" /><template v-if="r.cache_hit_ratio"> · cache {{ pct(r.cache_hit_ratio) }}</template>
                      </span>
                    </div>
                  </td>
                  <td class="n num">{{ r.runs }}</td>
                  <td class="n num" :title="`TB ${usd(r.avg_cost_usd)} / lượt`">{{ usd(r.cost_usd) }}</td>
                  <td class="n num">{{ dur(r.duration_s.avg) }}</td>
                  <td class="n num">{{ dur(r.duration_s.p90) }}</td>
                  <td class="n num" :class="{ bad: (r.failure_rate ?? 0) > 0.2 }" :title="`${r.failed + r.interrupted} lỗi · ${r.cancelled} huỷ`">{{ pct(r.failure_rate) }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="card steps-card" :class="{ dim: loading }">
            <span class="headline">{{ stepsByCost ? 'Bước tốn nhất' : 'Bước lâu nhất' }}</span>
            <ul v-if="steps.length" v-auto-animate class="steps">
              <li v-for="s in steps" :key="s.kind" :title="s.kind">
                <span class="s-row">
                  <span class="s-name">{{ stepTitle(s.kind) }}</span>
                  <span class="num s-v">{{ stepsByCost ? (s.cost_usd > 0 ? usd(s.cost_usd) : '—') : humanDuration(s.total_s) }}</span>
                </span>
                <span class="meter"><span :style="{ transform: `scaleX(${(stepsByCost ? s.cost_usd : s.total_s) / stepMax})` }" /></span>
                <span class="caption num">{{ s.steps }} lần · {{ humanDuration(s.total_s) }}<template v-if="s.cached"> · {{ s.cached }} cache</template><template v-if="s.failed"> · {{ s.failed }} lỗi</template></span>
              </li>
            </ul>
            <p v-else class="caption">Chưa có bước nào.</p>
          </div>
        </div>
      </template>
    </template>
  </section>
</template>

<style scoped>
.sec {
  display: grid;
  gap: 10px;
}
.sec-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}
.sec-head .section-label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.controls {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.dim {
  opacity: 0.6;
  transition: opacity var(--t-base);
}
.kpis,
.chart-card,
.table-card,
.steps-card {
  transition: opacity var(--t-base);
}
.skel-wrap {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  padding: 14px;
}
.skeleton {
  height: 56px;
  border-radius: 8px;
  position: relative;
  overflow: hidden;
  background: var(--bg-sunken);
}
.skeleton::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(100deg, transparent 20%, var(--bg-hover) 50%, transparent 80%);
  animation: shimmer 1.4s ease-in-out infinite;
}
.state {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
}
.stale {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--st-waiting);
}
.mini {
  height: 22px;
  padding: 0 6px;
  font-size: var(--fs-caption);
}
.kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
}
.kpi {
  display: grid;
  gap: 2px;
  padding: 12px 14px;
}
.kpi-v {
  font-size: var(--fs-title1);
  font-weight: 650;
  letter-spacing: -0.01em;
  line-height: 1.2;
}
.bad {
  color: var(--st-fail);
}
.idle {
  padding: 2px 2px 0;
}
.chart-card {
  display: grid;
  gap: 8px;
  padding: 12px 14px;
}
.chart-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.chart-body {
  display: grid;
  gap: 4px;
}
.y-max {
  min-height: 14px;
}
.chart {
  position: relative;
  height: 120px;
  touch-action: none;
}
.chart svg {
  display: block;
  overflow: visible;
}
.base {
  stroke: var(--separator-strong);
  stroke-width: 1;
}
.grid {
  stroke: var(--separator);
  stroke-dasharray: 2 3;
}
.bar {
  fill: var(--accent);
  transition:
    d var(--t-slow) var(--ease-out),
    opacity var(--t-fast);
}
.bar.off {
  opacity: 0.45;
}
@media (prefers-reduced-motion: reduce) {
  .bar {
    transition: opacity var(--t-fast);
  }
}
.chart-empty {
  position: absolute;
  inset: 0 0 12px;
  display: grid;
  place-items: center;
  pointer-events: none;
}
.tip {
  position: absolute;
  top: 6px;
  transform: translateX(-50%);
  display: grid;
  gap: 1px;
  padding: 6px 10px;
  border-radius: 8px;
  background: var(--bg-elevated);
  box-shadow: var(--shadow-2), var(--hairline);
  white-space: nowrap;
  pointer-events: none;
  z-index: 2;
}
.tip-v {
  font-weight: 650;
}
.x-axis {
  display: flex;
  justify-content: space-between;
}
.grid2 {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(220px, 1fr);
  gap: 12px;
  align-items: start;
}
@media (max-width: 900px) {
  .grid2 {
    grid-template-columns: minmax(0, 1fr);
  }
}
.table-card {
  padding: 4px 0;
  overflow-x: auto;
}
.tbl {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  font-size: var(--fs-callout);
}
.tbl th {
  text-align: left;
  padding: 0 8px;
  box-shadow: inset 0 -0.5px 0 var(--separator);
}
.tbl th:first-child,
.tbl td:first-child {
  padding-left: 14px;
}
.tbl th:last-child,
.tbl td:last-child {
  padding-right: 14px;
}
.tbl th.n,
.tbl td.n {
  text-align: right;
}
.th-btn {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  height: 26px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--text-2);
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}
.th-btn.on {
  color: var(--text);
}
.th-btn:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
  border-radius: 4px;
}
.tbl td {
  padding: 7px 8px;
  white-space: nowrap;
}
.tbl tr + tr td {
  box-shadow: inset 0 0.5px 0 var(--separator);
}
.name {
  display: grid;
  gap: 1px;
  max-width: 260px;
}
.r-name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
}
.steps-card {
  display: grid;
  gap: 10px;
  padding: 12px 14px;
}
.steps {
  display: grid;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.steps li {
  display: grid;
  gap: 3px;
  min-width: 0;
}
.s-row {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: var(--fs-callout);
}
.s-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.s-v {
  font-weight: 600;
}
.meter {
  display: block;
  height: 4px;
  border-radius: 2px;
  background: var(--bg-sunken);
  overflow: hidden;
}
.meter span {
  display: block;
  height: 100%;
  border-radius: 2px;
  background: var(--accent);
  transform-origin: left;
  transition: transform var(--t-slow) var(--ease-out);
}
.spin {
  animation: spin 0.9s linear infinite;
}
.busy {
  color: var(--text-3);
}
</style>
