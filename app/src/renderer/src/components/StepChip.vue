<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RotateCcw } from '@lucide/vue'
import type { ProgressData, StepRow } from '@/api/types'
import { humanDuration, kindName, STEP_LABEL, WAITING_LABEL } from '@/composables/format'
import StatusGlyph from './StatusGlyph.vue'
import LaneTag from './LaneTag.vue'
import ElapsedTimer from './ElapsedTimer.vue'
import TweenNumber from './TweenNumber.vue'

const props = defineProps<{ step: StepRow; progress?: ProgressData; selected?: boolean; retrying?: boolean }>()
const emit = defineEmits<{ select: []; retry: [] }>()

const fraction = computed(() => {
  const p = props.progress
  if (!p || !p.max) return null
  return Math.min(1, p.value / p.max)
})
const counter = computed(() => {
  const p = props.progress
  return p && p.max > 1 ? `${Math.min(p.max, Math.floor(p.value))}/${p.max}` : null
})
const nodeLabel = computed(() => props.progress?.label || props.progress?.node || null)

const detail = computed(() => {
  const st = props.step
  switch (st.status) {
    case 'waiting_resource':
      return WAITING_LABEL[String(st.meta.waiting_for ?? '')] ?? 'Chờ tài nguyên'
    case 'ready':
      return 'Sẵn sàng, đang xếp lượt'
    case 'awaiting_human':
      return st.kind === 'gate.pick' ? 'Chờ bạn chọn ảnh' : 'Chờ bạn duyệt'
    case 'succeeded':
      return st.started_at && st.ended_at ? `Xong sau ${humanDuration(st.ended_at - st.started_at)}` : 'Xong'
    case 'cached':
      return 'Kết quả cũ, không chạy lại'
    case 'failed':
      return st.error?.message ?? 'Lỗi không rõ'
    case 'running':
      return nodeLabel.value ?? 'Đang chạy'
    default:
      return STEP_LABEL[st.status]
  }
})
const detailKey = computed(() => (props.step.status === 'running' ? `running:${props.progress?.node ?? ''}` : props.step.status))

const shake = ref(false)
watch(
  () => props.step.status,
  (now, before) => {
    if (now === 'failed' && before && before !== 'failed') {
      shake.value = false
      requestAnimationFrame(() => (shake.value = true))
    }
  }
)
</script>

<template>
  <div
    class="chip"
    :class="[`st-${step.status}`, { selected, shake }]"
    role="button"
    tabindex="0"
    :aria-pressed="selected"
    :aria-label="`${kindName(step.kind)}: ${STEP_LABEL[step.status]}`"
    @click="emit('select')"
    @keydown.enter.prevent="emit('select')"
    @keydown.space.prevent="emit('select')"
    @animationend.self="shake = false"
  >
    <span v-if="step.status === 'running'" class="shimmer" aria-hidden="true" />
    <StatusGlyph :status="step.status" :progress="fraction" :size="26" />
    <div class="body">
      <div class="row1">
        <span class="name">{{ kindName(step.kind) }}</span>
        <span class="key">{{ step.key }}</span>
        <span v-if="step.attempt > 1" class="attempt">lần {{ step.attempt }}</span>
        <Transition name="pop">
          <span v-if="step.status === 'cached'" class="reuse">Dùng lại</span>
        </Transition>
        <span class="spacer" />
        <LaneTag :lane="step.lane" />
      </div>
      <div class="row2">
        <Transition name="swap" mode="out-in">
          <span :key="detailKey" class="detail" :class="{ err: step.status === 'failed' }">{{ detail }}</span>
        </Transition>
        <span v-if="step.status === 'running'" class="metrics num">
          <span v-if="counter">{{ counter }}</span>
          <span v-if="fraction !== null" class="pct"><TweenNumber :value="fraction * 100" />%</span>
          <ElapsedTimer v-if="step.started_at" :from="step.started_at" class="elapsed" />
        </span>
      </div>
      <div v-if="step.status === 'failed'" class="fail-actions">
        <span class="code">{{ step.error?.code }}</span>
        <button
          class="btn"
          :class="{ 'btn-primary': step.error?.retryable }"
          :disabled="retrying"
          @click.stop="emit('retry')"
        >
          <RotateCcw />
          Thử lại
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chip {
  position: relative;
  display: flex;
  gap: 12px;
  padding: 12px 14px;
  border-radius: var(--r-md);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-1), var(--hairline);
  cursor: pointer;
  overflow: hidden;
  transition:
    transform var(--t-fast) var(--ease-spring),
    opacity var(--t-base) var(--ease-out),
    box-shadow var(--t-base) var(--ease-out);
}
.chip:hover {
  box-shadow: var(--shadow-2), var(--hairline);
}
.chip:active {
  transform: scale(0.99);
}
.chip.selected {
  box-shadow: 0 0 0 2px var(--accent), var(--shadow-2);
}
.st-pending,
.st-skipped {
  opacity: 0.55;
}
.st-failed {
  background: color-mix(in srgb, var(--bg-elevated) 93%, var(--st-fail));
}
.st-awaiting_human {
  background: color-mix(in srgb, var(--bg-elevated) 93%, var(--st-human));
}
.shake {
  animation: gentle-shake 420ms var(--ease-out);
}
.shimmer {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: linear-gradient(100deg, transparent 20%, var(--st-running-soft) 50%, transparent 80%);
  animation: shimmer 1.8s var(--ease-out) infinite;
}
.body {
  position: relative;
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 2px;
  padding-top: 1px;
}
.row1 {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.name {
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.key {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--text-3);
}
.attempt {
  font-size: 11px;
  font-weight: 600;
  color: var(--st-waiting);
}
.reuse {
  font-size: 10.5px;
  font-weight: 700;
  padding: 1px 7px;
  border-radius: 999px;
  color: var(--st-cached);
  background: var(--st-cached-soft);
}
.spacer {
  flex: 1;
}
.row2 {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-height: 19px;
  color: var(--text-2);
  font-size: var(--fs-callout);
}
.detail {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.st-waiting_resource .detail {
  color: var(--st-waiting);
  font-weight: 600;
}
.st-awaiting_human .detail {
  color: var(--st-human);
  font-weight: 600;
}
.st-running .detail {
  color: var(--text);
}
.detail.err {
  color: var(--st-fail);
  white-space: normal;
  user-select: text;
}
.metrics {
  display: flex;
  gap: 10px;
}
.pct {
  color: var(--st-running);
  font-weight: 600;
  min-width: 4ch;
  text-align: right;
}
.elapsed {
  min-width: 4ch;
  text-align: right;
}
.fail-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
}
.code {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--text-3);
}
.swap-enter-active {
  transition:
    opacity var(--t-fast) var(--ease-out),
    transform var(--t-fast) var(--ease-out);
}
.swap-leave-active {
  transition: opacity 90ms var(--ease-in);
}
.swap-enter-from {
  opacity: 0;
  transform: translateY(3px);
}
.swap-leave-to {
  opacity: 0;
}
.pop-enter-active {
  transition:
    transform var(--t-base) var(--ease-spring),
    opacity var(--t-fast);
}
.pop-enter-from {
  transform: scale(0.6);
  opacity: 0;
}
</style>
