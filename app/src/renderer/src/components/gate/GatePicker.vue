<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { onKeyStroke, useElementSize } from '@vueuse/core'
import { Check, LoaderCircle, RefreshCw } from '@lucide/vue'
import type { BlobRef } from '@/api/types'
import Lightbox from '@/components/Lightbox.vue'
import { useGates, type GateItem } from '@/stores/gates'
import { useRuns } from '@/stores/runs'
import { useToasts } from '@/stores/toasts'
import GateTile from './GateTile.vue'
import { isTypingTarget, sourcePrompt } from './gateUtil'

const props = withDefaults(defineProps<{ gate: GateItem; keyboard?: boolean; inline?: boolean }>(), { keyboard: true, inline: false })
const emit = defineEmits<{ resolved: [] }>()

const gates = useGates()
const runs = useRuns()
const toasts = useToasts()

const picked = ref(new Set<string>())
const focus = ref(0)
const sending = ref(false)
const regenerating = ref(false)
const lightbox = ref<BlobRef | null>(null)

watch(
  () => props.gate.stepId,
  () => {
    picked.value = new Set()
    focus.value = 0
  }
)

const candidates = computed(() => props.gate.candidates)
const need = computed(() => (props.gate.k > 0 ? Math.min(props.gate.k, candidates.value.length) : 0))
const valid = computed(() => (need.value ? picked.value.size === need.value : picked.value.size >= 1))
const instruction = computed(() => (need.value ? `Chọn ${need.value} ảnh để chạy tiếp` : 'Chọn ít nhất 1 ảnh để chạy tiếp'))
const prompt = computed(() => sourcePrompt(runs.runs.get(props.gate.runId)))
const canRegenerate = computed(() => !!gates.sourceStep(props.gate))

function toggle(i: number): void {
  const b = candidates.value[i]
  if (!b) return
  focus.value = i
  const next = new Set(picked.value)
  if (next.has(b.hash)) next.delete(b.hash)
  else {
    if (need.value === 1) next.clear()
    else if (need.value && next.size >= need.value) return
    next.add(b.hash)
  }
  picked.value = next
}

async function submit(): Promise<void> {
  if (!valid.value || sending.value) return
  sending.value = true
  try {
    await gates.resolve(props.gate.stepId, { picked: [...picked.value] })
    toasts.push('Đã gửi lựa chọn, lượt chạy tiếp tục', 'success', 2500)
    emit('resolved')
  } catch (e) {
    toasts.error(e, 'Không gửi được lựa chọn')
  } finally {
    sending.value = false
  }
}

async function regenerate(): Promise<void> {
  if (regenerating.value) return
  regenerating.value = true
  try {
    await gates.regenerate(props.gate)
    toasts.push('Đang tạo lại ảnh với seed mới', 'info', 2500)
  } catch (e) {
    toasts.error(e, 'Không tạo lại được ảnh')
  } finally {
    regenerating.value = false
  }
}

const grid = ref<HTMLElement | null>(null)
const { width } = useElementSize(grid)
const cols = computed(() => Math.max(1, Math.floor((width.value + 14) / (200 + 14))))

onKeyStroke(true, (e) => {
  if (!props.keyboard || lightbox.value || isTypingTarget(e) || e.ctrlKey || e.metaKey || e.altKey) return
  const n = candidates.value.length
  if (/^[1-9]$/.test(e.key)) {
    const i = Number(e.key) - 1
    if (i < n) {
      e.preventDefault()
      toggle(i)
    }
    return
  }
  const move: Record<string, number> = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: cols.value, ArrowUp: -cols.value }
  if (e.key in move && n) {
    e.preventDefault()
    focus.value = Math.min(n - 1, Math.max(0, focus.value + move[e.key]!))
    return
  }
  if (e.key === ' ') {
    e.preventDefault()
    toggle(focus.value)
    return
  }
  if (e.key === 'Enter') {
    e.preventDefault()
    void submit()
  }
})
</script>

<template>
  <div class="gp" :class="{ inline }">
    <p v-if="prompt" class="prompt" :title="prompt">
      <span class="prompt-lbl">Prompt</span>
      <span class="prompt-txt selectable">{{ prompt }}</span>
    </p>

    <div ref="grid" class="sheet" role="listbox" :aria-multiselectable="need !== 1" :aria-label="instruction">
      <GateTile
        v-for="(b, i) in candidates"
        :key="b.hash"
        :blob="b"
        :index="i"
        :label="`Ảnh ${i + 1}`"
        :picked="picked.has(b.hash)"
        :focused="keyboard && focus === i"
        @click="toggle(i)"
        @dblclick="need === 1 && (toggle(i), submit())"
      />
    </div>

    <footer class="actions">
      <span v-if="keyboard && !inline" class="caption hints">
        <span class="kbd">1</span>–<span class="kbd">9</span> chọn · <span class="kbd">←</span><span class="kbd">→</span> di chuyển ·
        <span class="kbd">Enter</span> xác nhận
      </span>
      <button
        class="btn"
        :disabled="!canRegenerate || regenerating || sending"
        :title="canRegenerate ? 'Không ảnh nào ổn: chạy lại bước sinh ảnh với seed mới' : 'Không xác định được bước sinh ảnh'"
        @click="regenerate"
      >
        <LoaderCircle v-if="regenerating" class="spin" />
        <RefreshCw v-else />
        Tạo lại ảnh
      </button>
      <span class="spacer" />
      <span class="num secondary">{{ picked.size }}{{ need ? ` / ${need}` : '' }} đã chọn</span>
      <button class="btn btn-primary" :class="{ 'btn-lg': !inline }" :disabled="!valid || sending" @click="submit">
        <LoaderCircle v-if="sending" class="spin" />
        <Check v-else />
        Dùng ảnh đã chọn
      </button>
    </footer>

    <Lightbox :blob="lightbox" :siblings="candidates" @close="lightbox = null" />
  </div>
</template>

<style scoped>
.gp {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.prompt {
  display: flex;
  gap: 8px;
  align-items: baseline;
  margin: 0;
  padding: 0 24px 4px;
  min-width: 0;
  font-size: var(--fs-callout);
  color: var(--text-2);
}
.prompt-lbl {
  flex: none;
  font-size: var(--fs-caption);
  font-weight: 600;
  color: var(--text-3);
}
.prompt-txt {
  min-width: 0;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.sheet {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 12px 24px 24px;
  display: grid;
  /* Up to 4 per row before tiles drop under 200px, so a 4-up pick never leaves a lone tile on row 2. */
  grid-template-columns: repeat(auto-fill, minmax(min(200px, calc((100% - 42px) / 4)), 1fr));
  gap: 14px;
  align-content: start;
}
.actions {
  flex: none;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 24px;
  border-top: 0.5px solid var(--separator);
  background: color-mix(in srgb, var(--bg-content) 85%, transparent);
  backdrop-filter: blur(16px);
}
.hints {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}
.spacer {
  flex: 1;
}
.inline .prompt,
.inline .sheet,
.inline .actions {
  padding-left: 0;
  padding-right: 0;
}
.inline .sheet {
  overflow: visible;
  padding-top: 4px;
  padding-bottom: 0;
  grid-template-columns: repeat(auto-fill, minmax(min(140px, calc((100% - 30px) / 4)), 1fr));
  gap: 10px;
}
.inline .actions {
  flex-wrap: wrap;
  padding-top: 12px;
  padding-bottom: 0;
  background: none;
  backdrop-filter: none;
}
.spin {
  animation: spin 1s linear infinite;
}
</style>
