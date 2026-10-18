<script setup lang="ts">
import { computed } from 'vue'
import { LoaderCircle } from '@lucide/vue'
import { ART } from '@/assets/art'
import { useConnection } from '@/stores/connection'

const conn = useConnection()

const banner = computed(() => {
  const c = conn.core
  if (c.mock) return null
  if (conn.socket === 'auth_error') return { tone: 'bad', title: 'Core từ chối token', text: 'Khởi động lại app để cấp token mới.' }
  if (c.state === 'starting') return { tone: 'wait', title: 'Đang khởi động core', text: c.attempt > 1 ? `Lần thử ${c.attempt}` : 'Thường mất vài giây.' }
  if (c.state === 'restarting') {
    const secs = c.retryInMs ? Math.ceil(c.retryInMs / 1000) : 0
    return { tone: 'bad', title: c.message ?? 'Core đã dừng', text: `Tự khởi động lại sau ${secs} giây.`, detail: c.detail }
  }
  if (c.state === 'error') return { tone: 'bad', title: 'Không chạy được core', text: c.message ?? '', detail: c.detail }
  if (c.state === 'ready' && conn.socket === 'reconnecting') return { tone: 'wait', title: 'Mất kết nối sự kiện', text: 'Đang nối lại, dữ liệu sẽ tự đồng bộ.' }
  return null
})
</script>

<template>
  <Transition name="banner">
    <div v-if="banner" class="banner" :class="banner.tone" role="status" aria-live="polite">
      <LoaderCircle v-if="banner.tone === 'wait'" class="spin" :size="16" />
      <img v-else class="art" :src="ART.coreDisconnected" alt="" width="40" height="40" draggable="false" />
      <div class="txt">
        <strong>{{ banner.title }}</strong>
        <span>{{ banner.text }}</span>
        <pre v-if="banner.detail" class="detail selectable">{{ banner.detail }}</pre>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.banner {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  margin: 12px 24px 0;
  padding: 10px 14px;
  border-radius: var(--r-md);
  font-size: var(--fs-callout);
  box-shadow: var(--hairline);
}
.wait {
  background: var(--st-waiting-soft);
  color: var(--st-waiting);
}
.bad {
  background: var(--st-fail-soft);
  color: var(--st-fail);
}
.txt {
  display: grid;
  gap: 2px;
  color: var(--text);
}
.txt span {
  color: var(--text-2);
}
.detail {
  margin: 6px 0 0;
  font-family: var(--mono);
  font-size: 11px;
  white-space: pre-wrap;
  color: var(--text-2);
}
.art {
  flex: none;
  margin: -6px 0 -6px -4px;
  user-select: none;
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
</style>
