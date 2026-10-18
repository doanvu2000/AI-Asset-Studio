<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { onKeyStroke } from '@vueuse/core'
import { LoaderCircle, ThumbsDown, ThumbsUp } from '@lucide/vue'
import type { BlobRef } from '@/api/types'
import Lightbox from '@/components/Lightbox.vue'
import AssetViewer from '@/components/viewers/AssetViewer.vue'
import { featuredAsset, viewsFor } from '@/components/viewers/detect'
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

const sending = ref<'yes' | 'no' | null>(null)
const lightbox = ref<BlobRef | null>(null)
const focus = ref(0)
watch(
  () => props.gate.stepId,
  () => (focus.value = 0)
)

const candidates = computed(() => props.gate.candidates)
const featured = computed(() => {
  const c = candidates.value
  return featuredAsset(c) ?? (c[0] ? (viewsFor(c[0], c)[0] ?? null) : null)
})
const prompt = computed(() => sourcePrompt(runs.runs.get(props.gate.runId)))

async function resolve(approved: boolean): Promise<void> {
  if (sending.value) return
  sending.value = approved ? 'yes' : 'no'
  try {
    await gates.resolve(props.gate.stepId, { approved })
    toasts.push(approved ? 'Đã duyệt, lượt chạy tiếp tục' : 'Đã từ chối', approved ? 'success' : 'info', 2500)
    emit('resolved')
  } catch (e) {
    toasts.error(e, 'Không gửi được')
  } finally {
    sending.value = null
  }
}

function open(i: number): void {
  const b = candidates.value[i]
  if (!b) return
  focus.value = i
  lightbox.value = b
}

onKeyStroke(true, (e) => {
  if (!props.keyboard || lightbox.value || isTypingTarget(e) || e.ctrlKey || e.metaKey || e.altKey) return
  if (/^[1-9]$/.test(e.key) && Number(e.key) <= candidates.value.length) {
    e.preventDefault()
    open(Number(e.key) - 1)
  } else if (e.key === 'Enter') {
    e.preventDefault()
    void resolve(true)
  } else if (e.key === 'x' || e.key === 'X') {
    e.preventDefault()
    void resolve(false)
  }
})
</script>

<template>
  <div class="ga" :class="{ inline }">
    <p v-if="prompt" class="prompt" :title="prompt">
      <span class="prompt-lbl">Prompt</span>
      <span class="prompt-txt selectable">{{ prompt }}</span>
    </p>

    <div v-if="featured" class="feature card">
      <AssetViewer :key="featured.blob.hash" :view="featured" :compact="inline" />
    </div>

    <div v-if="candidates.length > 1" class="strip" role="listbox" aria-label="Các tệp kết quả">
      <GateTile
        v-for="(b, i) in candidates"
        :key="b.hash"
        :blob="b"
        :index="i"
        :label="b.name ?? `Tệp ${i + 1}`"
        :size="192"
        :focused="keyboard && focus === i"
        readonly
        @click="open(i)"
      />
    </div>

    <footer class="actions">
      <span v-if="keyboard && !inline" class="caption hints">
        <span class="kbd">Enter</span> duyệt · <span class="kbd">X</span> từ chối · <span class="kbd">1</span>–<span class="kbd">9</span> xem lớn
      </span>
      <span class="spacer" />
      <button class="btn btn-danger" :class="{ 'btn-lg': !inline }" :disabled="!!sending" @click="resolve(false)">
        <LoaderCircle v-if="sending === 'no'" class="spin" />
        <ThumbsDown v-else />
        Từ chối
      </button>
      <button class="btn btn-primary" :class="{ 'btn-lg': !inline }" :disabled="!!sending" @click="resolve(true)">
        <LoaderCircle v-if="sending === 'yes'" class="spin" />
        <ThumbsUp v-else />
        Duyệt
      </button>
    </footer>

    <Lightbox :blob="lightbox" :siblings="candidates" @close="lightbox = null" />
  </div>
</template>

<style scoped>
.ga {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.prompt {
  display: flex;
  gap: 8px;
  align-items: baseline;
  margin: 0;
  padding: 0 24px;
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
.feature {
  flex: 1 1 auto;
  min-height: 300px;
  margin: 0 24px;
  overflow: hidden;
  animation: fade-up var(--t-slow) var(--ease-out) backwards;
}
.strip {
  flex: none;
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: 96px;
  gap: 10px;
  overflow-x: auto;
  padding: 2px 24px 4px;
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
.actions .btn-danger {
  margin-right: 4px;
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
.inline .strip {
  padding-left: 0;
  padding-right: 0;
}
.inline .feature {
  flex: none;
  height: 360px;
  margin: 0;
}
.inline .actions {
  padding: 4px 0 0;
  border-top: 0;
  background: none;
  backdrop-filter: none;
}
.spin {
  animation: spin 1s linear infinite;
}
</style>
