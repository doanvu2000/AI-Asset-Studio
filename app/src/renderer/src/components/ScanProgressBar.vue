<script setup lang="ts">
import { computed } from 'vue'
import { X } from '@lucide/vue'
import type { ScanProgress } from '@/composables/scanProgress'

const props = defineProps<{
  progress: ScanProgress | null
  cancelling?: boolean
  unit?: string
  compact?: boolean
}>()
defineEmits<{ cancel: [] }>()

const unit = computed(() => props.unit ?? 'tệp')
const known = computed(() => !!props.progress && props.progress.total > 0)
const pct = computed(() => (known.value ? Math.min(100, (100 * props.progress!.done) / props.progress!.total) : 0))
const label = computed(() => {
  const p = props.progress
  if (props.cancelling) return 'Đang huỷ…'
  if (!p || p.phase === 'start') return 'Đang chuẩn bị…'
  if (p.phase === 'walk') return `Đang đọc cấu trúc thư mục · ${p.done.toLocaleString('vi-VN')} ${unit.value}`
  if (p.phase === 'analyze') return 'Đang phân tích ảnh trùng và asset thừa…'
  return `Đã quét ${p.done.toLocaleString('vi-VN')}/${p.total.toLocaleString('vi-VN')} ${unit.value}`
})
</script>

<template>
  <div class="sp" :class="{ compact }">
    <div class="line">
      <span class="lbl num" aria-live="polite">{{ label }}</span>
      <button type="button" class="btn btn-plain cancel" :disabled="cancelling" @click="$emit('cancel')"><X :size="14" /> Huỷ</button>
    </div>
    <div class="bar" role="progressbar" aria-label="Tiến độ quét" :aria-valuemin="0" :aria-valuemax="100" :aria-valuenow="known ? Math.round(pct) : undefined">
      <span v-if="known && progress?.phase === 'files'" class="fill" :style="{ transform: `scaleX(${pct / 100})` }" />
      <span v-else class="fill indet" />
    </div>
  </div>
</template>

<style scoped>
.sp {
  display: grid;
  gap: 8px;
  width: min(380px, 100%);
}
.sp.compact {
  width: min(260px, 100%);
  gap: 4px;
}
.line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 28px;
}
.lbl {
  font-size: 13px;
  color: var(--ink-2);
  font-variant-numeric: tabular-nums;
  text-align: left;
}
.cancel {
  flex: none;
}
.bar {
  position: relative;
  height: 6px;
  border-radius: 3px;
  background: var(--fill-2, var(--hair-soft));
  overflow: hidden;
}
.fill {
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: var(--accent);
  transform-origin: left center;
  transition: transform 220ms var(--ease-out);
}
.fill.indet {
  width: 35%;
  animation: sp-slide 1.1s var(--ease-out) infinite alternate;
}
@keyframes sp-slide {
  from {
    transform: translateX(0);
  }
  to {
    transform: translateX(185%);
  }
}
@media (prefers-reduced-motion: reduce) {
  .fill {
    transition: none;
  }
  .fill.indet {
    animation: none;
    width: 100%;
    opacity: 0.4;
  }
}
</style>
