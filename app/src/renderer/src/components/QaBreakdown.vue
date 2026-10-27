<script setup lang="ts">
import { computed, ref } from 'vue'
import { ChevronDown, LoaderCircle, RotateCw } from '@lucide/vue'
import type { QaResult } from '@/api/types'
import { QA_TIER_TEXT, qaScore, qaTier } from '@/composables/qa'
import QaBadge from './QaBadge.vue'

const props = defineProps<{ qa: QaResult | null | undefined; busy?: boolean; canRecompute?: boolean }>()
const emit = defineEmits<{ recompute: [] }>()
const open = ref(false)
const score = computed(() => qaScore(props.qa))
const summary = computed(() => {
  if (score.value === null) return props.qa?.note ?? 'Chưa chấm điểm'
  const issues = props.qa?.issues ?? []
  return issues.length ? `${QA_TIER_TEXT[qaTier(score.value)]} · ${issues.length} vấn đề` : QA_TIER_TEXT[qaTier(score.value)]
})
</script>

<template>
  <div class="qa-box">
    <div class="head">
      <button type="button" class="toggle" :aria-expanded="open" :disabled="score === null" @click="open = !open">
        <QaBadge v-if="score !== null" :qa="qa" size="md" />
        <span class="label">Điểm chất lượng</span>
        <span class="caption sum">{{ summary }}</span>
        <ChevronDown v-if="score !== null" class="chev" :class="{ open }" :size="14" aria-hidden="true" />
      </button>
      <button v-if="canRecompute" type="button" class="btn btn-plain re" :disabled="busy" title="Chấm lại bằng bản heuristic hiện tại" @click="emit('recompute')">
        <LoaderCircle v-if="busy" class="spin" :size="13" />
        <RotateCw v-else :size="13" />
        Chấm lại
      </button>
    </div>
    <Transition name="qa-open">
      <div v-if="open && qa?.parts?.length" class="body">
        <ul class="parts">
          <li v-for="p in qa.parts" :key="p.key" :class="`t-${qaTier(p.score)}`">
            <span class="pl">{{ p.label }}</span>
            <span class="bar" aria-hidden="true"><span :style="{ transform: `scaleX(${p.score / 100})` }" /></span>
            <span class="ps num">{{ p.score }}</span>
            <span class="caption pd">{{ p.detail }}</span>
          </li>
        </ul>
        <ul v-if="qa.issues?.length" class="issues caption">
          <li v-for="i in qa.issues" :key="i">{{ i }}</li>
        </ul>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.qa-box {
  display: grid;
  gap: 6px;
}
.head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.toggle {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 2px 0;
  border: 0;
  background: none;
  text-align: left;
  cursor: pointer;
}
.toggle:disabled {
  cursor: default;
}
.label {
  font-weight: 600;
}
.sum {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chev {
  color: var(--text-3);
  transition: transform var(--t-fast) var(--ease-out);
}
.chev.open {
  transform: rotate(180deg);
}
.re {
  height: 26px;
  padding: 0 8px;
  gap: 5px;
  font-size: var(--fs-caption);
}
.body {
  display: grid;
  gap: 8px;
  padding: 4px 0 2px;
}
.parts {
  display: grid;
  gap: 5px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.parts li {
  display: grid;
  grid-template-columns: 150px minmax(60px, 160px) 30px minmax(0, 1fr);
  align-items: center;
  gap: 10px;
}
.pl {
  font-size: var(--fs-callout);
}
.bar {
  height: 6px;
  border-radius: 3px;
  background: var(--bg-sunken);
  overflow: hidden;
}
.bar span {
  display: block;
  height: 100%;
  transform-origin: left;
  background: currentColor;
  transition: transform var(--t-slow) var(--ease-out);
}
.ps {
  font-weight: 650;
  text-align: right;
}
.pd {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.t-good .bar,
.t-good .ps {
  color: var(--st-ok);
}
.t-warn .bar,
.t-warn .ps {
  color: var(--st-waiting);
}
.t-bad .bar,
.t-bad .ps {
  color: var(--st-fail);
}
.issues {
  margin: 0;
  padding-left: 18px;
  color: var(--text-2);
}
.spin {
  animation: spin 1s linear infinite;
}
.qa-open-enter-active,
.qa-open-leave-active {
  transition:
    opacity var(--t-fast),
    transform var(--t-fast) var(--ease-out);
}
.qa-open-enter-from,
.qa-open-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
@media (prefers-reduced-motion: reduce) {
  .bar span,
  .chev,
  .qa-open-enter-active,
  .qa-open-leave-active {
    transition: none;
  }
}
</style>
