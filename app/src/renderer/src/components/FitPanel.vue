<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { LoaderCircle, RotateCw } from '@lucide/vue'
import type { FitEntry, FitLevel } from '@/api/types'
import { api } from '@/stores/connection'
import { useFit } from '@/stores/fit'
import { useFlows } from '@/stores/flows'
import { useRecipes } from '@/stores/recipes'
import { useToasts } from '@/stores/toasts'
import { bytes } from '@/composables/format'
import FitBadge from './FitBadge.vue'
import TweenNumber from './TweenNumber.vue'

const fit = useFit()
const recipes = useRecipes()
const flows = useFlows()
const toasts = useToasts()
const router = useRouter()

const refreshing = ref(false)
const installing = ref<string | null>(null)
const filter = ref<FitLevel | 'all'>('all')

onMounted(() => {
  void recipes.load()
  if (!flows.defsLoaded) void flows.loadDefs()
  void refresh()
})

async function refresh(): Promise<void> {
  refreshing.value = true
  await fit.load(true)
  refreshing.value = false
}

const MB = 1024 ** 2
const mb = (n: number | null | undefined): string => (n == null ? '?' : bytes(n * MB))
const TIER: Record<string, string> = { low: 'Cấu hình thấp', mid: 'Cấu hình trung bình', high: 'Cấu hình cao' }
const LEVELS: { id: FitLevel | 'all'; label: string }[] = [
  { id: 'all', label: 'Tất cả' },
  { id: 'green', label: 'Tốt' },
  { id: 'yellow', label: 'Chậm' },
  { id: 'red', label: 'Không đủ' }
]

interface Row {
  key: string
  name: string
  kind: 'recipe' | 'flow'
  id: string
  entry: FitEntry
}
const rows = computed<Row[]>(() => {
  const r = fit.report
  if (!r) return []
  const out: Row[] = [
    ...Object.entries(r.recipes ?? {}).map(([id, entry]) => ({ key: `r:${id}`, name: recipes.byId(id)?.name ?? id, kind: 'recipe' as const, id, entry })),
    ...Object.entries(r.flows ?? {}).map(([id, entry]) => ({ key: `f:${id}`, name: flows.byId(id)?.title ?? id, kind: 'flow' as const, id, entry }))
  ]
  return out.sort((a, b) => a.name.localeCompare(b.name, 'vi'))
})
const count = (l: FitLevel | 'all'): number => (l === 'all' ? rows.value.length : rows.value.filter((x) => x.entry.level === l).length)
const shown = computed(() => (filter.value === 'all' ? rows.value : rows.value.filter((x) => x.entry.level === filter.value)))

function open(x: Row): void {
  void router.push(x.kind === 'recipe' ? { path: '/new', query: { recipe: x.id } } : `/flows/${encodeURIComponent(x.id)}/run`)
}

async function install(id: string): Promise<void> {
  if (installing.value) return
  installing.value = id
  try {
    const { plan } = await api().setupPlan()
    await api().startSetup({ tools_dir: plan.tools_dir, models_dir: plan.models_dir, packs: [id], blender: false, claude: false })
    await router.push({ path: '/settings', query: { pane: 'setup' } })
  } catch (e) {
    toasts.error(e, 'Không bắt đầu cài được')
  } finally {
    installing.value = null
  }
}
</script>

<template>
  <div class="fitp">
    <header class="t-head">
      <div>
        <h2 class="content-title">Phù hợp với máy</h2>
        <p class="secondary">Cấu hình máy này và chức năng nào chạy tốt (xanh), chạy chậm hoặc thiếu gói (vàng), không đủ cấu hình (đỏ).</p>
      </div>
      <button class="btn" :disabled="refreshing" @click="refresh"><LoaderCircle v-if="refreshing" class="spin" /><RotateCw v-else /> Đo lại</button>
    </header>

    <p v-if="!fit.report" class="secondary" role="status">{{ refreshing ? 'Đang đọc cấu hình máy…' : 'Core chưa trả về thông tin cấu hình máy (cần bản core mới hơn).' }}</p>

    <template v-else>
      <section class="card hw" aria-label="Cấu hình máy">
        <div class="hw-cell">
          <span class="caption secondary">GPU</span>
          <span class="headline">{{ fit.report.hardware.gpu?.name ?? 'Không có GPU NVIDIA' }}</span>
          <span v-if="fit.report.hardware.gpu" class="caption secondary">{{ mb(fit.report.hardware.gpu.vram_total_mb) }} VRAM · trống {{ mb(fit.report.hardware.gpu.vram_free_mb) }}<template v-if="fit.report.hardware.gpu.driver"> · driver {{ fit.report.hardware.gpu.driver }}</template></span>
          <span v-else class="caption secondary">Các bước GPU sẽ không chạy được</span>
        </div>
        <div class="hw-cell">
          <span class="caption secondary">RAM</span>
          <span class="headline">{{ mb(fit.report.hardware.ram_total_mb) }}</span>
          <span class="caption secondary">trống {{ mb(fit.report.hardware.ram_free_mb) }}</span>
        </div>
        <div class="hw-cell">
          <span class="caption secondary">CPU</span>
          <span class="headline"><TweenNumber :value="fit.report.hardware.cpu_threads" /> luồng</span>
        </div>
        <div class="hw-cell">
          <span class="caption secondary">Ổ đĩa còn trống</span>
          <span class="headline">{{ mb(fit.report.hardware.disk_free_mb) }}</span>
        </div>
        <div class="hw-cell">
          <span class="caption secondary">Đánh giá chung</span>
          <span class="headline">{{ TIER[fit.report.hardware.tier] ?? fit.report.hardware.tier }}</span>
        </div>
      </section>
      <ul v-if="fit.report.recommend?.notes?.length" class="notes secondary">
        <li v-for="n in fit.report.recommend.notes" :key="n">{{ n }}</li>
      </ul>

      <section v-if="fit.report.recommend?.packs?.length">
        <p class="section-label">Gói model đề xuất</p>
        <div class="packs">
          <div v-for="p in fit.report.recommend.packs" :key="p.id" class="card pack">
            <span class="pk-line">
              <span class="headline">{{ p.label }}</span>
              <FitBadge :entry="{ level: p.level, reasons: [p.reason] }" />
            </span>
            <span class="caption secondary">{{ p.reason }}</span>
            <span class="pk-foot">
              <span class="caption secondary num">{{ mb(p.size_mb) }}</span>
              <span v-if="p.installed" class="caption done">Đã cài</span>
              <button v-else class="btn" :disabled="!!installing" @click="install(p.id)">
                <LoaderCircle v-if="installing === p.id" class="spin" />
                {{ p.level === 'red' ? 'Vẫn cài' : 'Cài gói' }}
              </button>
            </span>
          </div>
        </div>
      </section>

      <section>
        <p class="section-label">Chức năng</p>
        <div class="seg" role="radiogroup" aria-label="Lọc theo mức phù hợp">
          <button v-for="l in LEVELS" :key="l.id" type="button" role="radio" :aria-checked="filter === l.id" :class="{ on: filter === l.id }" @click="filter = l.id">
            {{ l.label }} <TweenNumber :value="count(l.id)" class="caption" />
          </button>
        </div>
        <ul class="list">
          <li v-for="x in shown" :key="x.key">
            <button type="button" class="card row" @click="open(x)">
              <FitBadge :entry="x.entry" />
              <span class="r-text">
                <span class="headline">{{ x.name }}<span v-if="x.kind === 'flow'" class="caption secondary"> · workflow</span></span>
                <span class="caption secondary">{{ x.entry.reasons[0] ?? 'Máy đủ cấu hình, chạy ở tốc độ tốt.' }}</span>
              </span>
            </button>
          </li>
        </ul>
        <p v-if="!shown.length" class="caption secondary">Không có chức năng nào ở mức này.</p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.fitp {
  max-width: 860px;
  margin: 0 auto;
  display: grid;
  gap: 18px;
  animation: fade-up var(--t-slow) var(--ease-out);
}
.content-title {
  font-size: 22px;
  font-weight: 600;
  line-height: 28px;
  letter-spacing: -0.015em;
}
.t-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.hw {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 14px 18px;
  padding: 14px 16px;
}
.hw-cell {
  display: grid;
  gap: 2px;
  min-width: 0;
  align-content: start;
}
.notes {
  margin: 0;
  padding-left: 18px;
  font-size: var(--fs-callout);
}
.section-label {
  margin-bottom: 8px;
}
.packs {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 10px;
}
.pack {
  display: grid;
  gap: 4px;
  align-content: start;
  padding: 12px 14px;
}
.pk-line,
.pk-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.pk-foot {
  margin-top: 4px;
}
.done {
  color: var(--st-ok);
  font-weight: 600;
}
.seg {
  margin-bottom: 10px;
}
.list {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.row {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 9px 14px;
  border: 0;
  font: inherit;
  color: inherit;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--t-fast) var(--ease-out);
}
.row:hover {
  background: var(--bg-hover);
}
.row :deep(.fit) {
  min-width: 76px;
  justify-content: flex-start;
}
.r-text {
  display: grid;
  gap: 1px;
  min-width: 0;
}
.spin {
  animation: spin 1s linear infinite;
}
@media (prefers-reduced-motion: reduce) {
  .fitp,
  .row {
    animation: none;
    transition: none;
  }
}
</style>
