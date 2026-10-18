<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useDocumentVisibility } from '@vueuse/core'
import { Cpu, Gpu, MemoryStick } from '@lucide/vue'
import type { DeviceInfo } from '../../../preload/types'
import TweenNumber from './TweenNumber.vue'

const POLL_MS = 3000
const GB = 1024 ** 3

const info = ref<DeviceInfo | null>(null)
const visible = useDocumentVisibility()
let timer: ReturnType<typeof setTimeout> | undefined
let alive = true

async function poll(): Promise<void> {
  const bridge = window.studio
  if (!bridge?.deviceInfo) return
  try {
    if (visible.value === 'visible') info.value = await bridge.deviceInfo()
  } catch {
    /* keep the last reading */
  }
  if (alive) timer = setTimeout(poll, POLL_MS)
}
onMounted(poll)
onBeforeUnmount(() => {
  alive = false
  clearTimeout(timer)
})

const gb = (n: number) => `${n.toLocaleString('vi-VN', { maximumFractionDigits: 1, minimumFractionDigits: 1 })}`
const shortGpu = (s: string) => s.replace(/^NVIDIA\s+/i, '').replace(/^GeForce\s+/i, '')
const shortCpu = (s: string) =>
  s
    .replace(/\((R|TM)\)/gi, '')
    .replace(/\s*(CPU|Processor)\b.*$/i, '')
    .replace(/\s+\d+-Core$/i, '')
    .replace(/\s+/g, ' ')
    .trim()

interface Cell {
  key: string
  label: string
  icon: unknown
  title: string
  used?: number
  total?: number
  sub: string
}

const cells = computed<Cell[]>(() => {
  const d = info.value
  if (!d) return []
  const out: Cell[] = []
  for (const [i, g] of d.gpus.entries()) {
    const extra = [g.util != null ? `tải ${g.util}%` : '', g.temp != null ? `${g.temp}°C` : ''].filter(Boolean).join(' · ')
    out.push({
      key: `gpu${i}`,
      label: 'GPU',
      icon: Gpu,
      title: `${shortGpu(g.name)}`,
      used: g.vramUsed / GB,
      total: g.vramTotal / GB,
      sub: `VRAM${extra ? ` · ${extra}` : ''}`
    })
  }
  out.push({ key: 'ram', label: 'RAM', icon: MemoryStick, title: '', used: d.ramUsed / GB, total: d.ramTotal / GB, sub: 'bộ nhớ hệ thống' })
  out.push({ key: 'cpu', label: 'CPU', icon: Cpu, title: shortCpu(d.cpu), sub: `${d.threads} luồng` })
  return out
})

const ratio = (c: Cell) => (c.total ? Math.min(1, (c.used ?? 0) / c.total) : 0)
</script>

<template>
  <section v-if="cells.length" class="dev card" aria-label="Thông số máy">
    <div v-for="c in cells" :key="c.key" class="cell" :title="c.key === 'cpu' ? info?.cpu : undefined">
      <p class="lbl caption">
        <component :is="c.icon" :size="13" aria-hidden="true" />
        {{ c.label }}
      </p>
      <p class="val">
        <template v-if="c.total != null">
          <span v-if="c.title" class="name">{{ c.title }}</span>
          <span class="nums">
            <TweenNumber :value="c.used ?? 0" :format="gb" /> / {{ gb(c.total) }} GB
          </span>
        </template>
        <span v-else class="name">{{ c.title }}</span>
      </p>
      <div v-if="c.total != null" class="meter" :class="{ high: ratio(c) > 0.9 }" role="meter" :aria-valuenow="Math.round(ratio(c) * 100)" aria-valuemin="0" aria-valuemax="100" :aria-label="c.label">
        <span :style="{ transform: `scaleX(${ratio(c)})` }" />
      </div>
      <p class="sub caption">{{ c.sub }}</p>
    </div>
  </section>
</template>

<style scoped>
.dev {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 0;
  padding: 4px;
}
.cell {
  display: grid;
  gap: 4px;
  align-content: start;
  padding: 10px 12px;
  min-width: 0;
}
.cell + .cell {
  box-shadow: inset 0.5px 0 0 var(--separator);
}
.lbl {
  display: flex;
  align-items: center;
  gap: 5px;
  margin: 0;
  color: var(--text-3);
}
.val {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin: 0;
  min-width: 0;
  font-weight: 600;
}
.name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.nums {
  flex: none;
  font-variant-numeric: tabular-nums;
  color: var(--text-2);
  font-weight: 500;
}
.val:not(:has(.name)) .nums {
  color: var(--text);
  font-weight: 600;
}
.meter {
  height: 4px;
  border-radius: 999px;
  background: var(--bg-sunken);
  overflow: hidden;
}
.meter span {
  display: block;
  height: 100%;
  background: var(--accent);
  transform-origin: left;
  transition: transform var(--t-slow) cubic-bezier(0.2, 0.8, 0.2, 1);
}
.meter.high span {
  background: var(--st-human);
}
.sub {
  margin: 0;
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
}
@media (prefers-reduced-motion: reduce) {
  .meter span {
    transition: none;
  }
}
</style>
