<script setup lang="ts">
import { computed, ref } from 'vue'
import { ClipboardCopy, LoaderCircle, RotateCw, X } from '@lucide/vue'
import { ART } from '@/assets/art'
import { useConnection } from '@/stores/connection'
import { useDiagnosticsActions } from '@/composables/diagnostics'
import { useCoreRestart } from '@/composables/coreRestart'

const conn = useConnection()
const diag = useDiagnosticsActions()
const restart = useCoreRestart()

const banner = computed(() => {
  const c = conn.core
  if (c.mock) return null
  if (conn.socket === 'auth_error') return { key: 'auth', tone: 'bad', title: 'Core từ chối token', text: 'Khởi động lại core để cấp token mới.', restart: true }
  if (c.state === 'starting') return { key: 'starting', tone: 'wait', title: 'Đang khởi động core', text: c.attempt > 1 ? `Lần thử ${c.attempt}` : 'Thường mất vài giây.' }
  if (c.state === 'restarting') {
    const secs = c.retryInMs ? Math.ceil(c.retryInMs / 1000) : 0
    return { key: `restarting:${c.attempt}`, tone: 'bad', title: c.message ?? 'Core đã dừng', text: `Tự khởi động lại sau ${secs} giây.`, detail: c.detail, restart: true }
  }
  if (c.state === 'error') return { key: `error:${c.attempt}`, tone: 'bad', title: 'Không chạy được core', text: c.message ?? '', detail: c.detail, restart: true }
  if (c.state === 'ready' && conn.socket === 'reconnecting') return { key: 'socket', tone: 'wait', title: 'Mất kết nối sự kiện', text: 'Đang nối lại, dữ liệu sẽ tự đồng bộ.' }
  return null
})

// Dismissal is per incident: the next failure (new attempt) shows the banner again.
const dismissed = ref<string | null>(null)
const visible = computed(() => (banner.value && banner.value.key !== dismissed.value ? banner.value : null))
</script>

<template>
  <Transition name="banner">
    <div v-if="visible" class="banner" :class="visible.tone" role="status" aria-live="polite">
      <LoaderCircle v-if="visible.tone === 'wait'" class="spin lead" :size="16" />
      <img v-else class="art" :src="ART.coreDisconnected" alt="" width="40" height="40" draggable="false" />
      <div class="txt">
        <strong>{{ visible.title }}</strong>
        <span>{{ visible.text }}</span>
        <pre v-if="visible.detail" class="detail selectable">{{ visible.detail }}</pre>
      </div>
      <div class="acts">
        <button v-if="visible.restart && restart.available" class="lnk" :disabled="restart.busy.value" @click="restart.run">
          <LoaderCircle v-if="restart.busy.value" class="spin" :size="13" />
          <RotateCw v-else :size="13" />
          Khởi động lại
        </button>
        <button v-if="diag.available" class="lnk" :disabled="!!diag.busy.value" @click="diag.copyLogs">
          <LoaderCircle v-if="diag.busy.value === 'copy'" class="spin" :size="13" />
          <ClipboardCopy v-else :size="13" />
          Copy nhật ký
        </button>
        <button class="close" aria-label="Ẩn thông báo" title="Ẩn" @click="dismissed = visible.key"><X :size="14" /></button>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
/* Floats under the toolbar so the drag region and caption buttons never move. */
.banner {
  position: absolute;
  top: calc(var(--toolbar-h) + 8px);
  left: 24px;
  right: 24px;
  z-index: 20;
  max-width: 760px;
  margin: 0 auto;
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 10px 10px 10px 14px;
  border-radius: var(--r-md);
  font-size: var(--fs-callout);
  box-shadow: var(--shadow-2), var(--hairline);
}
.wait {
  background: linear-gradient(var(--st-waiting-soft), var(--st-waiting-soft)), var(--bg-elevated);
  color: var(--st-waiting);
}
.bad {
  background: linear-gradient(var(--st-fail-soft), var(--st-fail-soft)), var(--bg-elevated);
  color: var(--st-fail);
}
.txt {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 2px;
  color: var(--text);
}
.txt span {
  color: var(--text-2);
}
.detail {
  max-height: 96px;
  overflow: auto;
  margin: 6px 0 0;
  font-family: var(--mono);
  font-size: 11px;
  white-space: pre-wrap;
  color: var(--text-2);
}
.acts {
  flex: none;
  display: flex;
  align-items: center;
  gap: 2px;
}
.lnk {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  padding: 0 8px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--accent);
  font: inherit;
  font-weight: 550;
  cursor: pointer;
  transition: background-color var(--t-fast);
}
.lnk:hover,
.close:hover {
  background: color-mix(in srgb, currentColor 10%, transparent);
}
.lnk:disabled {
  opacity: 0.6;
  cursor: default;
}
.close {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-3);
  cursor: pointer;
}
.art {
  flex: none;
  margin: -6px 0 -6px -4px;
  user-select: none;
}
.lead {
  margin-top: 1px;
}
.spin {
  animation: spin 1s linear infinite;
  flex: none;
}
.banner-enter-active {
  transition:
    transform var(--t-base) var(--ease-spring),
    opacity var(--t-base);
}
.banner-leave-active {
  transition: opacity var(--t-fast);
}
.banner-enter-from {
  transform: translateY(-8px);
  opacity: 0;
}
.banner-leave-to {
  opacity: 0;
}
@media (prefers-reduced-motion: reduce) {
  .banner-enter-from {
    transform: none;
  }
}
</style>
