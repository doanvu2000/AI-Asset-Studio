<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useEventListener } from '@vueuse/core'
import { Check, CircleAlert, Copy, LoaderCircle, ShieldCheck, X } from '@lucide/vue'
import type { AiCredits } from '@/api/types'
import { api } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'
import { useFocusTrap } from '@/composables/focusTrap'

const props = defineProps<{ open: boolean; projectId: string }>()
const emit = defineEmits<{ close: [] }>()

const toasts = useToasts()
const root = ref<HTMLElement | null>(null)
const closeBtn = ref<HTMLButtonElement | null>(null)
const isOpen = computed(() => props.open)
const trap = useFocusTrap(root, isOpen, closeBtn)

const loading = ref(false)
const credits = ref<AiCredits | null>(null)
const copied = ref(false)
let resetTimer = 0

watch(
  () => props.open,
  async (on) => {
    if (!on) return
    credits.value = null
    copied.value = false
    loading.value = true
    try {
      credits.value = await api().projectAiCredits(props.projectId)
    } catch (e) {
      toasts.error(e, 'Không tạo được bản khai báo AI')
    } finally {
      loading.value = false
    }
  }
)

async function copy(): Promise<void> {
  if (!credits.value) return
  try {
    if (window.studio) await window.studio.copyText(credits.value.markdown)
    else await navigator.clipboard.writeText(credits.value.markdown)
    copied.value = true
    window.clearTimeout(resetTimer)
    resetTimer = window.setTimeout(() => (copied.value = false), 1800)
  } catch (e) {
    toasts.error(e, 'Không copy được')
  }
}

useEventListener(
  document,
  'keydown',
  (e: KeyboardEvent) => {
    if (!props.open) return
    if (trap.onTab(e)) return
    if (e.key !== 'Escape') return
    e.preventDefault()
    e.stopPropagation()
    emit('close')
  },
  { capture: true }
)
</script>

<template>
  <Teleport to="body">
    <Transition name="dlg">
      <div v-if="open" ref="root" class="scrim" role="dialog" aria-modal="true" aria-labelledby="aic-title" @click.self="emit('close')">
        <section class="sheet">
          <header class="head">
            <ShieldCheck class="ico" aria-hidden="true" />
            <div class="who">
              <h2 id="aic-title" class="headline">Khai báo dùng AI</h2>
              <span class="caption">Mẫu cho Steam, Itch, Google Play, App Store · cũng nằm trong gói dưới tên AI_CREDITS.md</span>
            </div>
            <button ref="closeBtn" type="button" class="btn btn-plain btn-icon" aria-label="Đóng (Esc)" title="Đóng (Esc)" @click="emit('close')"><X /></button>
          </header>

          <div class="body">
            <p v-if="loading" class="caption state" role="status"><LoaderCircle :size="14" class="spin" /> Đang tổng hợp…</p>
            <template v-else-if="credits">
              <ul v-if="credits.warnings.length" class="warns" role="alert">
                <li v-for="w in credits.warnings" :key="w" class="caption"><CircleAlert :size="14" aria-hidden="true" /> {{ w }}</li>
              </ul>
              <pre class="code selectable">{{ credits.markdown }}</pre>
            </template>
          </div>

          <footer class="foot">
            <button type="button" class="btn btn-plain" @click="emit('close')">Đóng</button>
            <button type="button" class="btn btn-primary" :disabled="!credits" @click="copy">
              <Check v-if="copied" /> <Copy v-else /> {{ copied ? 'Đã copy' : 'Copy markdown' }}
            </button>
          </footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(0, 0, 0, 0.32);
}
.sheet {
  width: min(680px, 100%);
  max-height: 100%;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  background: var(--bg-elevated);
  border-radius: var(--r-xl);
  box-shadow: var(--shadow-3);
  overflow: hidden;
}
.head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 10px 12px 16px;
  border-bottom: 1px solid var(--hair-soft);
}
.ico {
  color: var(--accent);
  flex: none;
}
.who {
  flex: 1;
  min-width: 0;
  display: grid;
}
.body {
  overflow: auto;
  padding: 14px 16px;
  display: grid;
  gap: 10px;
  align-content: start;
}
.state {
  display: flex;
  align-items: center;
  gap: 6px;
}
.warns {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 6px;
  color: var(--st-fail);
}
.warns li {
  display: flex;
  gap: 6px;
  align-items: flex-start;
}
.code {
  margin: 0;
  padding: 10px 12px;
  border-radius: var(--r-md);
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
  font-family: var(--mono);
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}
.foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 16px;
  border-top: 1px solid var(--hair-soft);
}
.dlg-enter-active,
.dlg-leave-active {
  transition: opacity var(--t-base) var(--ease-out);
}
.dlg-enter-active .sheet,
.dlg-leave-active .sheet {
  transition: transform var(--t-base) var(--ease-spring);
}
.dlg-enter-from,
.dlg-leave-to {
  opacity: 0;
}
.dlg-enter-from .sheet,
.dlg-leave-to .sheet {
  transform: scale(0.97) translateY(6px);
}
</style>
