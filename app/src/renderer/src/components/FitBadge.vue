<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { Download } from '@lucide/vue'
import type { FitEntry, FitLevel } from '@/api/types'

const props = defineProps<{ entry: FitEntry | null | undefined; block?: boolean }>()

const LABEL: Record<FitLevel, string> = { green: 'Tốt', yellow: 'Chậm', red: 'Không đủ' }
const HEAD: Record<FitLevel, string> = {
  green: 'Hợp với máy của bạn',
  yellow: 'Chạy được nhưng có hạn chế',
  red: 'Máy không đủ cấu hình'
}

const level = computed(() => props.entry?.level)
const tip = computed(() => {
  const e = props.entry
  return e ? [HEAD[e.level], ...e.reasons].join('. ') : ''
})
const aria = computed(() => (props.entry ? `Độ phù hợp với máy: ${LABEL[props.entry.level]}. ${props.entry.reasons.join(' ')}` : ''))
const needsPack = computed(() => !!props.entry?.missing_packs?.length)
</script>

<template>
  <div v-if="entry && block" class="fit-block" :class="level" role="status">
    <span class="dot" aria-hidden="true" />
    <div class="fb-text">
      <p class="fb-head">{{ HEAD[entry.level] }}</p>
      <ul v-if="entry.reasons.length" class="fb-list">
        <li v-for="r in entry.reasons" :key="r">{{ r }}</li>
      </ul>
    </div>
    <RouterLink v-if="needsPack" :to="{ path: '/settings', query: { pane: 'setup' } }" class="btn btn-plain fb-link"><Download /> Cài gói còn thiếu</RouterLink>
  </div>
  <span v-else-if="entry" class="ui-pill fit" :class="level" :title="tip" :aria-label="aria" role="img">
    <span class="dot" aria-hidden="true" />{{ LABEL[entry.level] }}
  </span>
</template>

<style scoped>
.dot {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}
.green {
  color: var(--st-ok);
  background: var(--st-ok-soft);
}
.yellow {
  color: var(--st-waiting);
  background: var(--st-waiting-soft);
}
.red {
  color: var(--st-fail);
  background: var(--st-fail-soft);
}
.fit-block {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 14px;
  border-radius: var(--r-md);
  animation: fade-up var(--t-base) var(--ease-out);
}
.fit-block .dot {
  margin-top: 5px;
}
.fb-text {
  flex: 1;
  min-width: 0;
}
.fb-head {
  margin: 0;
  font-weight: 600;
  font-size: var(--fs-callout);
}
.fb-list {
  margin: 4px 0 0;
  padding-left: 16px;
  color: var(--text-2);
  font-size: var(--fs-callout);
}
.fb-link {
  flex: none;
  color: var(--text);
}
@media (prefers-reduced-motion: reduce) {
  .fit,
  .fit-block {
    transition: none;
    animation: none;
  }
}
</style>
