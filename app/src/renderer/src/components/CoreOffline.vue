<script setup lang="ts">
import { computed } from 'vue'
import { ClipboardCopy, LoaderCircle } from '@lucide/vue'
import { ART } from '@/assets/art'
import { useConnection } from '@/stores/connection'
import { useDiagnosticsActions } from '@/composables/diagnostics'

const conn = useConnection()
const diag = useDiagnosticsActions()

const info = computed(() => {
  const c = conn.core
  if (c.state === 'restarting') {
    const secs = c.retryInMs ? Math.ceil(c.retryInMs / 1000) : 0
    return { title: c.message ?? 'Core đã dừng', text: `Đang tự khởi động lại sau ${secs} giây.`, waiting: true }
  }
  if (c.state === 'stopped') return { title: 'Core chưa chạy', text: 'Khởi động lại app để bật core.', waiting: false }
  return { title: 'Không kết nối được core', text: c.message ?? 'Core không phản hồi.', waiting: false }
})
</script>

<template>
  <div class="offline view" role="alert">
    <img class="art" :src="ART.coreDisconnected" alt="" width="200" height="200" draggable="false" />
    <h2 class="title">{{ info.title }}</h2>
    <p class="secondary txt">
      <LoaderCircle v-if="info.waiting" class="spin" :size="14" />
      {{ info.text }}
    </p>
    <pre v-if="conn.core.detail" class="detail selectable">{{ conn.core.detail }}</pre>
    <button v-if="diag.available" class="btn logs" :disabled="!!diag.busy.value" @click="diag.copyLogs">
      <LoaderCircle v-if="diag.busy.value === 'copy'" class="spin" />
      <ClipboardCopy v-else />
      Copy nhật ký
    </button>
  </div>
</template>

<style scoped>
.offline {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 32px 24px 72px;
  text-align: center;
  animation: fade-up var(--t-slow) var(--ease-out);
}
.art {
  margin-bottom: 8px;
  user-select: none;
}
.title {
  margin: 0;
  font-size: var(--fs-title);
  font-weight: 600;
}
.txt {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  max-width: 52ch;
}
.detail {
  max-width: min(640px, 100%);
  max-height: 180px;
  overflow: auto;
  margin: 12px 0 0;
  padding: 10px 12px;
  border-radius: var(--r-md);
  font-family: var(--mono);
  font-size: 11px;
  text-align: left;
  white-space: pre-wrap;
  color: var(--text-2);
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
}
.logs {
  margin-top: 12px;
}
.spin {
  animation: spin 1s linear infinite;
}
</style>
